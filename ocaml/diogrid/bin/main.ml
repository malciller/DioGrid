open Core
open Dotenv
open Lwt.Syntax
open Yojson.Safe.Util
open Cohttp_lwt_unix
open Websocket
open Lwt.Infix

module B = Bytes

(*====================
TRADING CONFIGURATION
=====================*)
let tracked_pairs = [
(*Pair, grid_interval, order_qty, sell_multiplier, price_precision*)
  ("BTC/USD", 0.75, 0.001, 0.999, 1);
  ("XRP/USD", 2.5, 5.0, 0.999, 5);
  ("SOL/USD", 3.0, 0.07, 0.999, 2);
  ("ADA/USD", 2.5, 18.0, 0.999, 6); 
  ("ETH/USD", 3.0, 0.0047, 0.999, 2);
  ("TRX/USD", 3.0, 55.0, 0.999, 6);
  ("DOT/USD", 3.0, 2.5, 0.999, 4); 
  ("INJ/USD", 3.0, 0.85, 0.999, 3);
  ("KSM/USD", 3.0, 0.6, 0.999, 2); 
]

(*===========================
  KRAKEN API HELPER FUNCTIONS
=============================*)
type ws_token_info = {
  token: string;
  expires: int;
}

type instrument_details = {
  _symbol: string;
}

let instrument_details = ref []

let get_api_credentials_from_env () =
  let api_key = Sys.getenv_exn "KRAKEN_API_KEY" in
  let api_secret = Sys.getenv_exn "KRAKEN_API_SECRET" in
  (api_key, api_secret)

let base_url = "https://api.kraken.com"

let get_nonce () =
  Int64.to_string (Int64.of_float (Core_unix.gettimeofday () *. 1000.0))

let bytes_concat bs =
  let total_len = List.fold ~init:0 ~f:(fun acc b -> acc + B.length b) bs in
  let res = B.create total_len in
  let rec copy pos = function
    | [] -> ()
    | b :: bs ->
      B.blit ~src:b ~src_pos:0 ~dst:res ~dst_pos:pos ~len:(B.length b);
      copy (pos + B.length b) bs
  in
  copy 0 bs;
  res

let get_signature urlpath nonce data api_secret =
  (* Concatenate nonce and POST data *)
  let encoded = nonce ^ data in
  (* Compute SHA256 hash *)
  let sha256_hash = Digestif.SHA256.digest_string encoded in
  (* Create message: URL path concatenated with the raw SHA256 hash *)
  let message = bytes_concat [
    B.of_string urlpath;
    Digestif.SHA256.to_raw_string sha256_hash |> B.of_string
  ] in
  (* Decode API secret and compute HMAC-SHA512 *)
  let decoded_secret = Base64.decode_exn api_secret in
  let hmac = Digestif.SHA512.hmac_string ~key:decoded_secret (B.to_string message) in
  Base64.encode_exn (Digestif.SHA512.to_raw_string hmac)

let get_websocket_token api_key api_secret =
  let endpoint = "/0/private/GetWebSocketsToken" in
  let nonce = get_nonce () in
  let payload = "nonce=" ^ nonce in
  let signature = get_signature endpoint nonce payload api_secret in
  let uri = Uri.of_string (base_url ^ endpoint) in
  let headers = Cohttp.Header.of_list [
    ("API-Key", api_key);
    ("API-Sign", signature);
    ("Content-Type", "application/x-www-form-urlencoded");
  ] in
  let body = Cohttp_lwt.Body.of_string payload in
  
  let* (resp, body) = Client.post ~headers ~body uri in
  let* body_str = Cohttp_lwt.Body.to_string body in
  if Cohttp.Code.code_of_status (Cohttp.Response.status resp) = 200 then (
    let json = Yojson.Safe.from_string body_str in
    let error = json |> member "error" |> to_list in
    if List.length error > 0 then (
      let error_msg = String.concat ~sep:", " (List.map ~f:to_string error) in
      Lwt.fail_with ("API error: " ^ error_msg)
    ) else (
      let result = json |> member "result" in
      let token = result |> member "token" |> to_string in
      let expires = result |> member "expires" |> to_int in
      Lwt.return { token; expires }
    )
  ) else (
    Lwt.fail_with (Printf.sprintf "Failed to get WebSocket token: HTTP %d" 
      (Cohttp.Code.code_of_status (Cohttp.Response.status resp)))
  )

let safe_string_opt json field =
  try 
    match Yojson.Safe.Util.(member field json) with
    | `Null -> None
    | x -> Some (Yojson.Safe.Util.to_string x)
  with _ -> None

let safe_string json field default =
  match safe_string_opt json field with
  | Some v -> v
  | None -> default

let safe_int_opt json field =
  try 
    match Yojson.Safe.Util.(member field json) with
    | `Null -> None
    | x -> Some (Yojson.Safe.Util.to_int x)
  with _ -> None

let[@warning "-32"] safe_int json field default =
  match safe_int_opt json field with
  | Some v -> v
  | None -> default

let safe_float_opt json field =
  try 
    match Yojson.Safe.Util.(member field json) with
    | `Null -> None
    | x -> Some (Yojson.Safe.Util.to_float x)
  with _ -> None

let safe_float json field default =
  match safe_float_opt json field with
  | Some v -> v
  | None -> default

let safe_bool_opt json field =
  try
    match Yojson.Safe.Util.(member field json) with
    | `Bool b -> Some b
    | _ -> None
  with _ -> None

let[@warning "-32"] safe_bool json field default =
  match safe_bool_opt json field with
  | Some v -> v
  | None -> default

let debug_log msg =
  let timestamp = Core_unix.gettimeofday () |> Core_unix.localtime |> fun tm ->
    Printf.sprintf "%04d-%02d-%02d %02d:%02d:%02d"
      (tm.tm_year + 1900) (tm.tm_mon + 1) tm.tm_mday
      tm.tm_hour tm.tm_min tm.tm_sec
  in
  if String.is_prefix msg ~prefix:"Error" ||
     String.is_prefix msg ~prefix:"[PRIVATE]" ||
     String.is_prefix msg ~prefix:"API error" ||
     String.is_prefix msg ~prefix:"[PRICE DEBUG]" ||
     String.is_prefix msg ~prefix:"[CACHE" ||
     String.is_prefix msg ~prefix:"[AMEND" || 
     String.is_prefix msg ~prefix:"[RATE LIMIT]" ||
     String.is_prefix msg ~prefix:"[ORDER"
  then Lwt_io.printf "[%s] %s\n" timestamp msg
  else Lwt.return_unit


(* Update public subscribe/unsubscribe messages *)
let instrument_subscribe_message () =
  Yojson.Safe.to_string (`Assoc [
    "method", `String "subscribe";
    "params", `Assoc [
      "channel", `String "instrument";
      "snapshot", `Bool true;
    ];
    "req_id", `Int 1
  ])

let instrument_unsubscribe_message () =
  Yojson.Safe.to_string (`Assoc [
    "method", `String "unsubscribe";
    "params", `Assoc [
      "channel", `String "instrument";
    ];
    "req_id", `Int 2
  ])

let ticker_subscribe_message pairs =
  Yojson.Safe.to_string (`Assoc [
    "method", `String "subscribe";
    "params", `Assoc [
      "channel", `String "ticker";
      "symbol", `List (List.map ~f:(fun (p, _, _, _, _) -> `String p) pairs);
    ];
    "req_id", `Int 3
  ])

type ticker_data = {
  _symbol: string;
  current_price: float;
  _volume: float;
}

type order_side = Buy | Sell | Unknown

type order_status = 
  | PendingNew
  | New 
  | PartiallyFilled
  | Filled
  | Canceled
  | Expired

type order = {
  order_id: string;
  limit_price: float;
  order_symbol: string;
  side: order_side;
  status: order_status;
}

type order_validity_status = {
  status: [`Valid | `Invalid | `None];
  current_price: float;
  price_diff_pct: float;
  order_id: string option;
}

let ticker_cache : (string, ticker_data) Hashtbl.t = Hashtbl.create (module String)

let parse_instrument_data json =
  try 
    let pairs = json |> member "data" |> member "pairs" |> to_list in
    let* filtered_pairs = Lwt_list.filter_map_s (fun pair_json ->
      try 
        let symbol = safe_string pair_json "symbol" "" in
        if List.exists ~f:(fun (pair, _, _, _, _) -> String.equal pair symbol) tracked_pairs then
          let details = {
            _symbol = symbol;
          } in
          instrument_details := details :: !instrument_details;
          Lwt.return (Some details)
        else Lwt.return None
      with e -> 
        Printf.eprintf "Error parsing pair: %s\n" (Exn.to_string e);
        Lwt.return None
    ) pairs in
    Lwt.return filtered_pairs
  with e ->
    let* () = debug_log (Printf.sprintf "Error in parse_instrument_data: %s" (Exn.to_string e)) in
    Lwt.return []

let parse_order_side = function
  | "buy" -> Buy
  | "sell" -> Sell
  | "" -> Unknown  (* Handle empty string case *)
  | s -> 
      (* Log the unknown side but don't make it an Lwt operation *)
      Printf.eprintf "[ORDER] Unknown order side: '%s'\n" s;
      Unknown  (* Handle any other unexpected values *)

let parse_order_status = function
  | "pending_new" -> PendingNew
  | "new" -> New
  | "partially_filled" -> PartiallyFilled
  | "filled" -> Filled
  | "canceled" -> Canceled
  | "expired" -> Expired
  | s -> raise (Invalid_argument (Printf.sprintf "Invalid order status: %s" s))

let open_buy_orders = Hashtbl.create (module String)

let format_order_side = function
  | Buy -> "BUY"
  | Sell -> "SELL"
  | Unknown -> "UNKNOWN"

let format_order_status = function
  | PendingNew -> "PENDING"
  | New -> "NEW"
  | PartiallyFilled -> "PARTIAL"
  | Filled -> "FILLED"
  | Canceled -> "CANCELED"
  | Expired -> "EXPIRED"

let format_order_log order action =
  Printf.sprintf "[ORDER %s] %s %s @ %f %s (ID: %s)"
    action
    (format_order_side order.side)
    order.order_symbol
    order.limit_price
    (format_order_status order.status)
    order.order_id

let log_open_orders () =
  let order_count = Hashtbl.length open_buy_orders in
  let* () = debug_log (Printf.sprintf "[ORDER SUMMARY] %d open buy orders:" order_count) in
  let orders = Hashtbl.to_alist open_buy_orders in
  Lwt_list.iter_s (fun (_key, order) ->
    debug_log (format_order_log order "ACTIVE")
  ) orders



(* temporary cache for pending orders *)
let pending_orders : (string, order) Hashtbl.t = Hashtbl.create (module String)

(* Add a small delay after order cancellation to ensure state is consistent *)
let handle_order_cancellation order_id _symbol =
  let* () = debug_log (Printf.sprintf "[ORDER CANCELED] Processing cancellation for %s" order_id) in
  Hashtbl.remove open_buy_orders order_id;
  Hashtbl.remove pending_orders order_id;
  (* Add a small delay to ensure state propagation *)
  let* () = Lwt_unix.sleep 1.0 in
  Lwt.return_unit

let parse_execution_message json =
  Lwt.catch
    (fun () ->
      let channel = json |> member "channel" |> to_string in
      match channel with
      | "executions" ->
          let msg_type = json |> member "type" |> to_string in
          let data = json |> member "data" |> to_list in
          let* () = debug_log (Printf.sprintf "[PRIVATE] Processing %s message with %d orders" 
            msg_type (List.length data)) in
          
          Lwt_list.iter_s (fun order_json ->
            let order_id = safe_string order_json "order_id" "" in
            let exec_type = safe_string order_json "exec_type" "" in
            let symbol = safe_string order_json "symbol" "" in
            
            match exec_type with
            | "canceled" ->
                (match Hashtbl.find open_buy_orders order_id with
                 | Some existing_order ->
                     let* () = debug_log (format_order_log existing_order exec_type) in
                     let* () = handle_order_cancellation order_id symbol in
                     log_open_orders ()
                 | None -> Lwt.return_unit)
            | "filled" | "expired" ->
                (match Hashtbl.find open_buy_orders order_id with
                 | Some existing_order ->
                     Hashtbl.remove open_buy_orders order_id;
                     Hashtbl.remove pending_orders order_id;
                     let* () = debug_log (format_order_log existing_order exec_type) in
                     log_open_orders ()
                 | None -> Lwt.return_unit)
            | _ -> (* Handle other message types normally *)
                (* Get symbol/side, preserving existing values for amendments *)
                let symbol = 
                  match exec_type with
                  | "amended" ->
                      (match Hashtbl.find open_buy_orders order_id with
                       | Some existing_order -> existing_order.order_symbol
                       | None -> safe_string order_json "symbol" "")
                  | "new" ->
                      (match Hashtbl.find pending_orders order_id with
                       | Some pending_order -> pending_order.order_symbol
                       | None -> safe_string order_json "symbol" "")
                  | _ -> safe_string order_json "symbol" ""
                in
                
                let side = 
                  match exec_type with
                  | "amended" ->
                      (match Hashtbl.find open_buy_orders order_id with
                       | Some existing_order -> existing_order.side
                       | None -> 
                           let side_str = safe_string order_json "side" "" in
                           parse_order_side side_str)
                  | "new" ->
                      (match Hashtbl.find pending_orders order_id with
                       | Some pending_order -> pending_order.side
                       | None ->
                           let side_str = safe_string order_json "side" "" in
                           parse_order_side side_str)
                  | _ ->
                      let side_str = safe_string order_json "side" "" in
                      parse_order_side side_str
                in
                
                (* Rest of the existing processing for non-terminal states *)
                match side with
                | Buy when List.exists tracked_pairs ~f:(fun (pair, _, _, _, _) -> 
                    String.equal pair symbol) ->
                  let status_str = safe_string order_json "order_status" "" in
                  let status = parse_order_status status_str in
                  let limit_price = 
                    match exec_type with
                    | "new" ->
                        (match Hashtbl.find pending_orders order_id with
                         | Some pending_order -> pending_order.limit_price
                         | None -> safe_float order_json "limit_price" 0.0)
                    | _ -> safe_float order_json "limit_price" 0.0
                  in

                  (* Create order record *)
                  let order = {
                    order_id;
                    order_symbol = symbol;
                    side;
                    status;
                    limit_price;
                  } in

                  let* msg = match exec_type with
                  | "pending_new" ->
                      (* Store in pending cache *)
                      Hashtbl.set pending_orders ~key:order_id ~data:order;
                      Lwt.return (format_order_log order "PENDING")
                  | "new" ->
                      (* Move from pending to open orders *)
                      Hashtbl.set open_buy_orders ~key:order_id ~data:order;
                      Hashtbl.remove pending_orders order_id;
                      Lwt.return (format_order_log order "NEW")
                  | "trade" ->
                      let last_qty = safe_float order_json "last_qty" 0.0 in
                      let last_price = safe_float order_json "last_price" 0.0 in
                      Lwt.return (Printf.sprintf "[ORDER FILL] %f %s at %.2f" 
                        last_qty order.order_symbol last_price)
                  | "amended" ->
                      let existing_order = Hashtbl.find open_buy_orders order_id in
                      let new_limit_price = safe_float order_json "limit_price"
                        (Option.value_map existing_order ~default:0.0 
                           ~f:(fun o -> o.limit_price)) in
                      let updated_order = {
                        order_id;
                        order_symbol = symbol;
                        side;
                        status;
                        limit_price = new_limit_price;
                      } in
                      let* () = debug_log (Printf.sprintf 
                        "[CACHE UPDATE] %s Before amendment - Order %s: %.8f"
                        symbol order_id (Option.value_map existing_order ~default:0.0 
                                  ~f:(fun o -> o.limit_price))) in
                      Hashtbl.set open_buy_orders ~key:order_id ~data:updated_order;
                      let* () = debug_log (Printf.sprintf 
                        "[CACHE UPDATE] After amendment - Order %s: %.8f"
                        order_id updated_order.limit_price) in
                      let* () = debug_log (Printf.sprintf
                        "[ORDER AMENDED] Updated limit price for %s to %.8f"
                        order_id new_limit_price) in
                      (* Verify cache update immediately *)
                      (match Hashtbl.find open_buy_orders order_id with
                      | Some verified_order ->
                          let* () = debug_log (Printf.sprintf 
                            "[CACHE VERIFY] Order %s now has limit price %.8f"
                            order_id verified_order.limit_price) in
                          Lwt.return (format_order_log updated_order "AMENDED")
                      | None ->
                          let msg = Printf.sprintf "[CACHE ERROR] Order %s not found after update" 
                            order_id in
                          let* () = debug_log msg in
                          Lwt.return msg)
                  | "restated" ->
                      (* Always update the order in our cache *)
                      Hashtbl.set open_buy_orders ~key:order_id ~data:order;
                      let reason = safe_string order_json "reason" "unknown" in
                      Lwt.return (Printf.sprintf "[ORDER RESTATED] %s: %s" order_id reason)
                  | "status" ->
                      (* Always update the order in our cache *)
                      Hashtbl.set open_buy_orders ~key:order_id ~data:order;
                      Lwt.return (format_order_log order "STATUS")
                  | _ -> 
                      Lwt.return (format_order_log order "UPDATE")
                  in

                  let* () = debug_log msg in
                  if String.equal exec_type "new" || 
                     String.equal exec_type "amended" ||
                     String.equal exec_type "restated" then
                    log_open_orders ()
                  else
                    Lwt.return_unit
                | _ -> Lwt.return_unit  (* Skip non-buy orders or untracked pairs *)
          ) data
      | _ -> 
          let* () = debug_log (Printf.sprintf "[DEBUG] Ignoring unknown channel: %s" channel) in
          Lwt.return_unit)
    (fun e ->
      debug_log (Printf.sprintf "[PRIVATE] Error parsing execution: %s" (Exn.to_string e)))

let private_subscribe_message token =
  Yojson.Safe.to_string (`Assoc [
    "method", `String "subscribe";
    "params", `Assoc [
      "channel", `String "executions";
      "token", `String token;
    ];
    "req_id", `Int 4
  ])


let getprice_precision symbol =
  match List.find tracked_pairs ~f:(fun (pair, _, _, _, _) -> String.equal pair symbol) with
  | Some (_, _, _, _, precision) -> precision
  | None -> 2

let format_precision value precision =
  let factor = 10.0 ** float_of_int precision in
  Float.round_down (value *. factor) /. factor



type amend_response = {
  success: bool;
  error: string option;
  result: Yojson.Safe.t option;
}

(* Keep the private connection plus request promise resolvers in one record *)
type private_conn_state = {
  conn: Websocket_lwt_unix.conn;
  token: string;
  response_promises: (int, amend_response Lwt.u) Hashtbl.t;
  mutable _last_active: float;
}

type operation_status = {
  timestamp: float;
  in_progress: bool;
}

let pending_operations : (string, operation_status) Hashtbl.t = Hashtbl.create (module String)

let is_operation_in_progress operation_key =
  match Hashtbl.find pending_operations operation_key with
  | None -> Lwt.return false
  | Some status ->
      if not status.in_progress then Lwt.return false
      else
        (* Check if the operation has timed out (10 seconds) *)
        let now = Core_unix.gettimeofday () in
        if Float.(now -. status.timestamp > 10.0) then (
          (* Clear timed out operation *)
          let* () = debug_log (Printf.sprintf "[TIMEOUT] Operation %s timed out and was cleared" 
            operation_key) in
          Hashtbl.remove pending_operations operation_key;
          Lwt.return false
        ) else
          Lwt.return true

let mark_operation_started operation_key =
  let status = {
    timestamp = Core_unix.gettimeofday ();
    in_progress = true;
  } in
  Hashtbl.set pending_operations ~key:operation_key ~data:status

let mark_operation_completed operation_key =
  Hashtbl.remove pending_operations operation_key

(* Rate limiting implementation *)
let rate_counters : (string, float) Hashtbl.t = Hashtbl.create (module String)
let rate_threshold = 60.0  (* Maximum rate per minute *)

let get_rate_counter symbol =
  let now = Core_unix.gettimeofday () in
  match Hashtbl.find rate_counters symbol with
  | None -> 0.0
  | Some last_rate ->
      (* Decay the rate based on time passed *)
      let time_passed = now -. (now |> Float.round_down) in
      let decayed_rate = last_rate *. Float.exp (-. time_passed *. 0.2) in  (* 5-second half-life *)
      decayed_rate

let update_rate_counter symbol cost =
  let current_rate = get_rate_counter symbol in
  let new_rate = current_rate +. cost in
  Hashtbl.set rate_counters ~key:symbol ~data:new_rate;
  new_rate

let rec wait_for_rate_limit symbol transaction_cost =
  let current_rate = get_rate_counter symbol in
  if Float.(current_rate +. transaction_cost > rate_threshold) then (
    let* () = debug_log (Printf.sprintf "[RATE LIMIT] %s - Current: %.2f, Waiting for decay..." 
      symbol current_rate) in
    let* () = Lwt_unix.sleep 2.0 in
    wait_for_rate_limit symbol transaction_cost
  ) else
    Lwt.return_unit

(* Updated amend_order function to use the unified private connection *)
let rec amend_order_with_retry (pconn : private_conn_state) symbol order_id current_price grid_interval price_precision retries =
  (* Add rate limit check before attempting amendment *)
  let* () = wait_for_rate_limit symbol 4.0 in  (* 1.0 for fixed + 3.0 for <5s decay *)
  
  let operation_key = Printf.sprintf "amend_%s" order_id in
  if retries <= 0 then
    let* () = debug_log (Printf.sprintf "[AMEND] Out of retries for order %s" order_id) in
    Lwt.return_unit
  else
    let* is_in_progress = is_operation_in_progress operation_key in
    if is_in_progress then (
      let* () = debug_log (Printf.sprintf "[AMEND] Operation in progress for %s - waiting 2 seconds before retry (%d left)" 
        order_id retries) in
      let* () = Lwt_unix.sleep 2.0 in
      amend_order_with_retry pconn symbol order_id current_price grid_interval price_precision (retries - 1)
    ) else (
      mark_operation_started operation_key;
      let order_qty =
        match List.find tracked_pairs ~f:(fun (pair, _, _, _, _) -> String.equal pair symbol) with
        | Some (_, _, qty, _, _) -> qty
        | None -> raise (Invalid_argument (Printf.sprintf "Symbol %s not found in tracked pairs" symbol))
      in
      let new_limit_price_raw = current_price *. (1.0 -. (grid_interval /. 100.0)) in
      let new_limit_price = format_precision new_limit_price_raw price_precision in
      let req_id = Random.int 10000 + 1 in
      let amend_request = `Assoc [
        "method", `String "amend_order";
        "params", `Assoc [
          "order_id", `String order_id;
          "limit_price", `Float new_limit_price;
          "order_qty", `Float order_qty;
          "post_only", `Bool true;
          "token", `String pconn.token;
        ];
        "req_id", `Int req_id;
      ] in
      let amend_request_str = Yojson.Safe.to_string amend_request in
      let* () = debug_log (Printf.sprintf "[AMEND] Preparing request for %s: New limit price %.8f (%.2f%% below %.8f)" 
        order_id new_limit_price grid_interval current_price) in
      let response_promise, response_resolver = Lwt.wait () in
      Hashtbl.set pconn.response_promises ~key:req_id ~data:response_resolver;
      Lwt.catch
        (fun () ->
          let frame = Frame.create ~content:amend_request_str () in
          let* () = Websocket_lwt_unix.write pconn.conn frame in
          let* () = debug_log "[AMEND] Request sent" in
          let* response = Lwt.pick [
            response_promise;
            (let* () = Lwt_unix.sleep 5.0 in
             Hashtbl.remove pconn.response_promises req_id;
             Lwt.return { success = false; error = Some "Timeout"; result = None })
          ] in
          mark_operation_completed operation_key;
          match response with
          | { success = true; result = Some result; error = None } ->
              let amend_id = safe_string result "amend_id" "unknown" in
              let amended_order_id = safe_string result "order_id" order_id in
              let* () = debug_log (Printf.sprintf "[AMEND] Success - amend_id: %s, order_id: %s, new price: %.8f" 
                amend_id amended_order_id new_limit_price) in
              (* Update rate counter on successful amendment *)
              let _ = update_rate_counter symbol 4.0 in
              Lwt.return_unit
          | { success = false; error = Some error_msg; _ } ->
              let* () = debug_log (Printf.sprintf "[AMEND] Failed - order_id: %s, error: %s" 
                order_id error_msg) in
              let* () = Lwt_unix.sleep 2.0 in
              amend_order_with_retry pconn symbol order_id current_price grid_interval price_precision (retries - 1)
          | _ ->
              let* () = debug_log (Printf.sprintf "[AMEND] Unexpected response for order %s" order_id) in
              let* () = Lwt_unix.sleep 2.0 in
              amend_order_with_retry pconn symbol order_id current_price grid_interval price_precision (retries - 1)
        )
        (fun e ->
          mark_operation_completed operation_key;
          let* () = debug_log (Printf.sprintf "[AMEND] Error: %s - retrying..." (Exn.to_string e)) in
          let* () = Lwt_unix.sleep 2.0 in
          amend_order_with_retry pconn symbol order_id current_price grid_interval price_precision (retries - 1))
    )


let has_existing_order symbol =
  let open_exists =
    Hashtbl.to_alist open_buy_orders
    |> List.exists ~f:(fun (_, order) -> String.equal order.order_symbol symbol) in
  let pending_exists =
    Hashtbl.to_alist pending_orders
    |> List.exists ~f:(fun (_, order) -> String.equal order.order_symbol symbol) in
  let* () = debug_log (Printf.sprintf "[ORDER CHECK] %s has_existing_order: open=%b pending=%b" 
    symbol open_exists pending_exists) in
  if open_exists || pending_exists then
    Lwt.return true
  else
    (* Add a small delay to ensure we don't race with order cancellation *)
    let* () = Lwt_unix.sleep 0.5 in
    let open_exists =
      Hashtbl.to_alist open_buy_orders
      |> List.exists ~f:(fun (_, order) -> String.equal order.order_symbol symbol) in
    let pending_exists =
      Hashtbl.to_alist pending_orders
      |> List.exists ~f:(fun (_, order) -> String.equal order.order_symbol symbol) in
    let* () = debug_log (Printf.sprintf "[ORDER CHECK] %s has_existing_order (recheck): open=%b pending=%b" 
      symbol open_exists pending_exists) in
    Lwt.return (open_exists || pending_exists)


let amend_order pconn symbol order_id current_price grid_interval price_precision =
  amend_order_with_retry pconn symbol order_id current_price grid_interval price_precision 3

(* Updated place_orders function to use the unified private connection *)
let place_orders (pconn : private_conn_state) symbol current_price =
  (* Add rate limit check before placing orders *)
  let* () = wait_for_rate_limit symbol 2.0 in  (* 1.0 each for buy and sell orders *)
  
  let* has_order = has_existing_order symbol in
  if has_order then (
    let* () = debug_log (Printf.sprintf "[ORDER SKIP] %s - Order already exists" symbol) in
    Lwt.return_unit
  ) else (
    let (_, grid_interval, order_qty, sell_multiplier, price_precision) =
      match List.find tracked_pairs ~f:(fun (pair, _, _, _, _) ->
        String.equal pair symbol) with
      | Some params -> params
      | None -> raise (Invalid_argument (Printf.sprintf "Symbol %s not found in tracked pairs" symbol))
    in
    let sell_qty = order_qty *. sell_multiplier in
    
    (* Calculate raw prices *)
    let sell_price_raw = current_price *. (1.0 +. (grid_interval /. 100.0)) in
    let buy_price_raw = current_price *. (1.0 -. (grid_interval /. 100.0)) in
    
    (* Debug log raw prices *)
    let* () = debug_log (Printf.sprintf "[PRICE DEBUG] %s Raw prices - Sell: %.8f, Buy: %.8f" 
      symbol sell_price_raw buy_price_raw) in
    
    (* Format prices according to symbol precision *)
    let (sell_limit_price, buy_limit_price) = 
      if String.equal symbol "BTC/USD" then (
        let sell_rounded = Float.round_decimal ~decimal_digits:0 sell_price_raw in
        let buy_rounded = Float.round_decimal ~decimal_digits:0 buy_price_raw in
        (sell_rounded, buy_rounded)
      ) else (
        (format_precision sell_price_raw price_precision,
         format_precision buy_price_raw price_precision)
      )
    in

    (* Debug log final prices *)
    let* () = debug_log (Printf.sprintf "[PRICE DEBUG] %s Final prices - Sell: %s, Buy: %s" 
      symbol 
      (if String.equal symbol "BTC/USD" then Printf.sprintf "%.0f" sell_limit_price else Printf.sprintf "%.8f" sell_limit_price)
      (if String.equal symbol "BTC/USD" then Printf.sprintf "%.0f" buy_limit_price else Printf.sprintf "%.8f" buy_limit_price)
    ) in

    (* Place sell order *)
    let sell_req_id = Random.int 10000 + 1 in
    let sell_request = `Assoc [
      "method", `String "add_order";
      "params", `Assoc [
        "symbol", `String symbol;
        "side", `String "sell";
        "order_type", `String "limit";
        "limit_price", `Float sell_limit_price;
        "order_qty", `Float sell_qty;
        "time_in_force", `String "gtc";
        "post_only", `Bool true;
        "token", `String pconn.token;
      ];
      "req_id", `Int sell_req_id;
    ] in
    let* () = debug_log (Printf.sprintf "[ORDER PLACE] Sending SELL order for %s: %.8f @ %.1f" 
      symbol sell_qty sell_limit_price) in
    let sell_frame = Frame.create ~content:(Yojson.Safe.to_string sell_request) () in
    let* () = Websocket_lwt_unix.write pconn.conn sell_frame in
    
    (* Wait for sell order response *)
    let sell_promise, sell_resolver = Lwt.wait () in
    Hashtbl.set pconn.response_promises ~key:sell_req_id ~data:sell_resolver;
    let* sell_response = Lwt.pick [
      sell_promise;
      (let* () = Lwt_unix.sleep 5.0 in
       Hashtbl.remove pconn.response_promises sell_req_id;
       Lwt.return { success = false; error = Some "Timeout"; result = None })
    ] in

    (* Log sell order result but continue regardless *)
    let* () = match sell_response with
    | { success = true; result = Some result; _ } ->
        let order_id = safe_string result "order_id" "unknown" in
        debug_log (Printf.sprintf "[ORDER PLACE] SELL order placed successfully: %s" order_id)
    | { error = Some error_msg; _ } ->
        debug_log (Printf.sprintf "[ORDER PLACE] SELL order failed: %s" error_msg)
    | _ -> 
        debug_log "[ORDER PLACE] Unexpected sell response"
    in
        
    (* Place buy order *)
    let buy_req_id = Random.int 10000 + 1 in
    let buy_request = `Assoc [
      "method", `String "add_order";
      "params", `Assoc [
        "symbol", `String symbol;
        "side", `String "buy";
        "order_type", `String "limit";
        "limit_price", `Float buy_limit_price;
        "order_qty", `Float order_qty;
        "time_in_force", `String "gtc";
        "post_only", `Bool true;
        "token", `String pconn.token;
      ];
      "req_id", `Int buy_req_id;
    ] in
    let* () = debug_log (Printf.sprintf "[ORDER PLACE] Sending BUY order for %s: %.8f @ %.1f" 
      symbol order_qty buy_limit_price) in
    let buy_frame = Frame.create ~content:(Yojson.Safe.to_string buy_request) () in
    let* () = Websocket_lwt_unix.write pconn.conn buy_frame in
    
    (* Wait for buy order response *)
    let buy_promise, buy_resolver = Lwt.wait () in
    Hashtbl.set pconn.response_promises ~key:buy_req_id ~data:buy_resolver;
    let* buy_response = Lwt.pick [
      buy_promise;
      (let* () = Lwt_unix.sleep 5.0 in
       Hashtbl.remove pconn.response_promises buy_req_id;
       Lwt.return { success = false; error = Some "Timeout"; result = None })
    ] in
    
    let* () = match buy_response with
    | { success = true; result = Some result; _ } ->
        let order_id = safe_string result "order_id" "unknown" in
        debug_log (Printf.sprintf "[ORDER PLACE] BUY order placed successfully: %s" order_id)
    | { error = Some error_msg; _ } ->
        let* () = debug_log (Printf.sprintf "[ORDER PLACE] BUY order failed: %s" error_msg) in
        Lwt.return_unit
    | _ -> 
        let* () = debug_log "[ORDER PLACE] Unexpected buy response" in
        Lwt.return_unit
    in
    (* Update rate counter after successful order placement *)
    let _ = update_rate_counter symbol 2.0 in
    Lwt.return_unit
  )

(* Updated check_order_validity function to use the unified private connection *)
let check_order_validity (pconn : private_conn_state) symbol =
  (* Get current price from ticker cache *)
  let current_price =
    match Hashtbl.find ticker_cache symbol with
    | Some ticker -> ticker.current_price
    | None -> raise (Invalid_argument (Printf.sprintf "No price data for symbol %s" symbol))
  in

  (* Find the grid interval for this symbol *)
  let (_, grid_interval, _, _, _) =
    match List.find tracked_pairs ~f:(fun (pair, _, _, _, _) -> String.equal pair symbol) with
    | Some params -> params
    | None -> raise (Invalid_argument (Printf.sprintf "Symbol %s not found in tracked pairs" symbol))
  in

  (* Look for an existing buy order in our open order cache *)
  let existing_order =
    Hashtbl.to_alist open_buy_orders
    |> List.find ~f:(fun (_, order) -> String.equal order.order_symbol symbol)
    |> Option.map ~f:snd
  in

  match existing_order with
  | Some order ->
      (* Calculate price difference percentage before rounding *)
      let price_diff_pct =
        Float.abs ((order.limit_price -. current_price) /. current_price *. 100.0)
      in
      let validity_status = {
        status = if Float.(price_diff_pct <= (2.0 *. grid_interval)) then `Valid else `Invalid;
        current_price;
        price_diff_pct;
        order_id = Some order.order_id;
      } in
      let* () = debug_log (Printf.sprintf "[ORDER CHECK] %s - %s  Current Price: %.8f Diff: %.2f%% (max %.2f%%)"
        symbol
        (match validity_status.status with | `Valid -> "VALID" | `Invalid -> "INVALID" | `None -> "NONE")
        current_price
        validity_status.price_diff_pct
        (2.0 *. grid_interval))
      in
      (match validity_status.status with
       | `Invalid ->
           (match validity_status.order_id with
            | Some order_id ->
                let price_precision = getprice_precision symbol in
                let* () = debug_log (Printf.sprintf "[ORDER AMEND] Amending order %s (limit price: %.8f)" order_id order.limit_price) in
                amend_order pconn symbol order_id validity_status.current_price grid_interval price_precision
            | None -> Lwt.return_unit)
       | _ -> Lwt.return_unit)
  | None ->
      let validity_status = {
        status = `None;
        current_price;
        price_diff_pct = 0.0;
        order_id = None;
      } in
      let* () = debug_log (Printf.sprintf "[ORDER VALIDITY] %s - No Order Found (current price: %.8f)" symbol validity_status.current_price) in
      let* has_order = has_existing_order symbol in
      if has_order then
        debug_log (Printf.sprintf "[ORDER SKIP] %s - Order already exists or pending" symbol)
      else
        place_orders pconn symbol current_price

(* Updated read_frames function to use the unified private connection *)
let rec read_frames public_conn (pconn : private_conn_state) =
  Lwt.catch
    (fun () ->
      Websocket_lwt_unix.read public_conn >>= fun frame ->
      let message = frame.content in
      
      (* Parse the JSON message safely *)
      let json_opt =
        try Some (Yojson.Safe.from_string message)
        with _ -> None
      in

      match json_opt with
      | None -> 
          let* () = debug_log "Failed to parse JSON message" in
          read_frames public_conn pconn
      | Some json ->
          let channel_opt = json |> member "channel" |> to_string_option in
          let event_opt = json |> member "event" |> to_string_option in
          
          match (event_opt, channel_opt) with
          | (_, Some "ticker") ->
              (try
                let data_arr = json |> member "data" |> to_list in
                match data_arr with
                | [ticker] -> 
                    let symbol = safe_string ticker "symbol" "Unknown" in
                    let current_price = safe_float ticker "last" 0.0 in
                    let* () = debug_log (Printf.sprintf "[TICKER] Received update for %s: %.8f" 
                      symbol current_price) in
                    
                    (* Check if this symbol is in our tracked pairs *)
                    let is_tracked = List.exists tracked_pairs ~f:(fun (pair, _, _, _, _) -> 
                      String.equal pair symbol) in
                    
                    if is_tracked then (
                      let ticker_data = {
                        _symbol = symbol;
                        current_price;
                        _volume = safe_float ticker "volume" 0.0;
                      } in
                      Hashtbl.set ticker_cache ~key:symbol ~data:ticker_data;
                      
                      (* Check order validity with our unified private connection *)
                      let* () = check_order_validity pconn symbol in
                      read_frames public_conn pconn
                    ) else
                      read_frames public_conn pconn
                | _ -> read_frames public_conn pconn
              with e -> 
                let* () = debug_log (Printf.sprintf "[TICKER] Error processing ticker: %s" 
                  (Exn.to_string e)) in
                read_frames public_conn pconn)
          | (_, Some "instrument") ->
              let data_type = json |> member "type" |> to_string_option in
              let* () = debug_log (Printf.sprintf "Instrument message type: %s" 
                (Option.value data_type ~default:"none")) in
              (match data_type with
              | Some "snapshot" ->
                  let* () = debug_log "Processing instrument snapshot..." in
                  let* tracked = parse_instrument_data json in
                  let* () = debug_log (Printf.sprintf "Processed %d tracked pairs" (List.length tracked)) in
                  (* After getting snapshot, unsubscribe from instrument and subscribe to ticker *)
                  let unsub_frame = Frame.create ~content:(instrument_unsubscribe_message ()) () in
                  let* () = debug_log "Unsubscribing from instrument channel..." in
                  let* () = Websocket_lwt_unix.write public_conn unsub_frame in
                  let sub_ticker_frame = Frame.create ~content:(ticker_subscribe_message tracked_pairs) () in
                  let* () = debug_log "Subscribing to ticker channel..." in
                  let* () = Websocket_lwt_unix.write public_conn sub_ticker_frame in
                  read_frames public_conn pconn
              | _ -> 
                  let* () = debug_log "Skipping non-snapshot instrument message" in
                  read_frames public_conn pconn)
          | _ -> 
              let* () = debug_log "Received unknown message type" in
              read_frames public_conn pconn)
    (fun e ->
      let* () = debug_log (Printf.sprintf "Error in read_frames: %s" (Exn.to_string e)) in
      read_frames public_conn pconn)

(* Updated connect_public function to use the unified private connection *)
let rec connect_public (pconn : private_conn_state) retries =
  let* () = debug_log (Printf.sprintf "Attempting public connection (retries left: %d)" retries) in
  if retries <= 0 then 
    debug_log "Out of retries for public connection" >>= fun () ->
    Lwt.return_unit
  else
    let url = Uri.of_string "wss://ws.kraken.com/v2" in
    let ctx = Lazy.force Conduit_lwt_unix.default_ctx in
    Lwt.catch
      (fun () ->
        let* () = debug_log "Connecting to WebSocket..." in
        Websocket_lwt_unix.connect ~ctx 
          (`TLS (`Hostname "ws.kraken.com", 
                `IP (Ipaddr.of_string_exn "104.16.248.94"), 
                `Port 443)) url >>= fun public_conn ->
        let* () = debug_log "Connected successfully" in
        let sub_msg = instrument_subscribe_message () in
        let* () = debug_log (Printf.sprintf "Sending instrument subscribe message: %s" sub_msg) in
        let frame = Frame.create ~content:sub_msg () in
        Websocket_lwt_unix.write public_conn frame >>= fun () ->
        read_frames public_conn pconn)
      (fun e ->
        debug_log (Printf.sprintf "Connection error: %s" (Exn.to_string e)) >>= fun () ->
        Lwt_unix.sleep 2.0 >>= fun () ->
        connect_public pconn (retries - 1))

(* Update the private connection function to return our new state record *)
let rec connect_private token retries : private_conn_state Lwt.t =
  let* () = debug_log (Printf.sprintf "\n[PRIVATE] Attempting private connection (retries left: %d)" retries) in
  if retries <= 0 then 
    Lwt.fail (Failure "[PRIVATE] Out of retries for private connection")
  else
    let url = Uri.of_string "wss://ws-auth.kraken.com/v2" in
    let ctx = Lazy.force Conduit_lwt_unix.default_ctx in
    Lwt.catch
      (fun () ->
        let* () = debug_log "[PRIVATE] Connecting to private WebSocket..." in
        let* private_conn = 
          try
            Websocket_lwt_unix.connect ~ctx 
              (`TLS (`Hostname "ws-auth.kraken.com", 
                    `IP (Ipaddr.of_string_exn "104.16.248.94"), 
                    `Port 443)) url
          with e ->
            let* () = debug_log (Printf.sprintf "[PRIVATE] Connection exception: %s" (Exn.to_string e)) in
            Lwt.fail e
        in
        let* () = debug_log "[PRIVATE] Connected successfully to private feed" in
        let sub_msg = private_subscribe_message token in
        let* () = debug_log (Printf.sprintf "[PRIVATE] Sending private subscribe message: %s" sub_msg) in
        let frame = Frame.create ~content:sub_msg () in
        let* () = 
          try
            Websocket_lwt_unix.write private_conn frame
          with e ->
            let* () = debug_log (Printf.sprintf "[PRIVATE] Write exception: %s" (Exn.to_string e)) in
            Lwt.fail e
        in
        
        let private_conn_state = {
          conn = private_conn;
          token = token;
          response_promises = Hashtbl.create (module Int);
          _last_active = Core_unix.gettimeofday ();
        } in
        Lwt.return private_conn_state)
      (fun e ->
        let* () = debug_log (Printf.sprintf "[PRIVATE] Connection error: %s" (Exn.to_string e)) in
        let* () = Lwt_unix.sleep 2.0 in
        connect_private token (retries - 1))

(* Unified read loop for private connection that handles both execution updates and command responses *)
let rec read_private_frames (pconn : private_conn_state) =
  Lwt.catch
    (fun () ->
      Websocket_lwt_unix.read pconn.conn >>= fun frame ->
      let message = frame.content in
      
      (* Parse the JSON message safely *)
      let json_opt =
        try Some (Yojson.Safe.from_string message)
        with _ -> None
      in

      match json_opt with
      | None -> 
          let* () = debug_log "Failed to parse JSON message" in
          read_private_frames pconn
      | Some json ->
          let channel_opt = json |> member "channel" |> to_string_option in
          let event_opt = json |> member "event" |> to_string_option in
          
          match (event_opt, channel_opt) with
          | (_, Some "executions") ->
              let* () = parse_execution_message json in
              read_private_frames pconn
          | (_, Some "instrument") ->
              let* () = debug_log "Received instrument message" in
              read_private_frames pconn
          | _ -> 
              let* () = debug_log "Received unknown message type" in
              read_private_frames pconn)
    (fun e ->
      let* () = debug_log (Printf.sprintf "Error in read_private_frames: %s" (Exn.to_string e)) in
      read_private_frames pconn)

let main () =
  printf "Starting Kraken API application...\n";
  try
    let (api_key, api_secret) = get_api_credentials_from_env () in
    printf "API credentials loaded successfully.\n";
    Lwt_main.run (
      Lwt.catch
        (fun () ->
          let* ws_token = get_websocket_token api_key api_secret in
          printf "[PRIVATE] WebSocket Token obtained: %s (expires in %d seconds)\n" 
            ws_token.token ws_token.expires;
          
          (* Create a single private connection *)
          Lwt.catch
            (fun () ->
              let* pconn = connect_private ws_token.token 3 in
              
              Lwt.join [
                connect_public pconn 3;
                read_private_frames pconn
              ])
            (fun exn ->
              printf "Connection setup error: %s\n" (Exn.to_string exn);
              Lwt.return_unit))
        (fun exn ->
          printf "Error: %s\n" (Exn.to_string exn);
          Lwt.return_unit)
    )
  with 
  | Failure msg when String.is_substring ~substring:"environment variable" msg ->
      printf "Error: Missing environment variables. Please make sure you have a .env file with KRAKEN_API_KEY and KRAKEN_API_SECRET defined.\n"
  | e -> 
      printf "Unexpected error: %s\n" (Exn.to_string e)

let () =
  (* Load environment variables from .env file *)
  export () |> ignore;
  main ()

