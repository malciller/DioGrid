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
  ("BTC/USD", 0.75, 0.000875, 0.99, 1);
  ("ETH/USD", 3.0, 0.0045, 0.99, 2);
  ("XRP/USD", 3.0, 5.0, 0.99, 5);
  ("SOL/USD", 2.5, 0.06, 0.99, 2);
  ("ADA/USD", 3.0, 18.0, 0.99, 6);
  ("TRX/USD", 3.0, 55.0, 0.99, 6);
  ("DOT/USD", 3.0, 2.5, 0.99, 4);
  ("INJ/USD", 3.0, 0.81, 0.99, 3);
  ("KSM/USD", 3.0, 0.6, 0.99, 2);
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
  price_precision: int;
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

let safe_int json field default =
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


let safe_bool json field default =
  match safe_bool_opt json field with
  | Some v -> v
  | None -> default

let debug_log msg =
  if String.is_prefix msg ~prefix:"[ORDER" ||
     String.is_prefix msg ~prefix:"[AMEND]" 
  then Lwt_io.printf "%s\n" msg
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

type order_side = Buy | Sell

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
            price_precision = safe_int pair_json "price_precision" 2;
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


(* Add order parsing functions *)
let parse_order_side = function
  | "buy" -> Buy
  | "sell" -> Sell
  | s -> raise (Invalid_argument (Printf.sprintf "Invalid order side: %s" s))

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



(* Add a temporary cache for pending orders *)
let pending_orders : (string, order) Hashtbl.t = Hashtbl.create (module String)

let parse_execution_message json =
  Lwt.catch
    (fun () ->
      (* First check if this is a subscription response *)
      if member "method" json |> to_string_option |> Option.is_some then
        let* () = debug_log "[PRIVATE] Received subscription confirmation" in
        Lwt.return_unit
      else
        let channel = json |> member "channel" |> to_string in
        match channel with
        | "heartbeat" -> Lwt.return_unit
        | "status" -> 
            let* () = debug_log "[PRIVATE] Received status message" in
            Lwt.return_unit
        | "executions" ->
            let msg_type = json |> member "type" |> to_string in
            let data = json |> member "data" |> to_list in
            let* () = debug_log (Printf.sprintf "[PRIVATE] Processing %s message with %d orders" 
              msg_type (List.length data)) in
            
            Lwt_list.iter_s (fun order_json ->
              let order_id = safe_string order_json "order_id" "" in
              let exec_type = safe_string order_json "exec_type" "" in
              
              (* For terminal states (canceled/filled/expired), just remove from book if exists *)
              match exec_type with
              | "canceled" | "filled" | "expired" ->
                  (match Hashtbl.find open_buy_orders order_id with
                   | Some existing_order ->
                       Hashtbl.remove open_buy_orders order_id;
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
                          "[CACHE UPDATE] Before amendment - Order %s: %.8f"
                          order_id (Option.value_map existing_order ~default:0.0 
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
            let* () = debug_log (Printf.sprintf "[PRIVATE] Ignoring unknown channel: %s" channel) in
            Lwt.return_unit)
    (fun e ->
      debug_log (Printf.sprintf "[PRIVATE] Error parsing execution: %s" (Exn.to_string e)))

(* Add private WebSocket message constructors *)
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
  match List.find !instrument_details ~f:(fun d -> String.equal d._symbol symbol) with
  | Some details -> details.price_precision
  | None -> 2


(* Add message handling types *)
type amend_response = {
  success: bool;
  error: string option;
  result: Yojson.Safe.t option;
}

type order_connection = {
  conn: Websocket_lwt_unix.conn;
  token: string;
  response_promises: (int, amend_response Lwt.u) Hashtbl.t;
}

let create_order_connection conn token =
  {
    conn;
    token;
    response_promises = Hashtbl.create (module Int);
  }

let rec read_order_connection_frames amend_conn =
  Lwt.catch
    (fun () ->
      let* frame = Websocket_lwt_unix.read amend_conn.conn in
      let msg = frame.content in
      let* () = debug_log (Printf.sprintf "[AMEND READ] Received: %s" msg) in
      
      (* Parse the message *)
      let json = Yojson.Safe.from_string msg in
      let req_id = safe_int json "req_id" (-1) in
      
      (* If this is a response to a pending request, resolve its promise *)
      if req_id >= 0 then
        match Hashtbl.find_and_remove amend_conn.response_promises req_id with
        | Some resolver ->
            let success = safe_bool json "success" false in
            let error = 
              match member "error" json with
              | `List errors -> Some (String.concat ~sep:", " (List.map ~f:to_string errors))
              | `String error -> Some error
              | `Null -> None
              | _ -> Some "Unknown error"
            in
            let result = 
              match member "result" json with
              | `Null -> None
              | result -> Some result
            in
            Lwt.wakeup resolver { success; error; result };
            read_order_connection_frames amend_conn
        | None ->
            let* () = debug_log (Printf.sprintf "[AMEND READ] No pending request for req_id %d" req_id) in
            read_order_connection_frames amend_conn
      else
        read_order_connection_frames amend_conn)
    (function
      | End_of_file ->
          debug_log "[AMEND READ] Connection closed"
      | exn ->
          debug_log (Printf.sprintf "[AMEND READ] Error: %s" (Exn.to_string exn)))

let rec connect_dedicated_order_connection token retries =
  let* () = debug_log (Printf.sprintf "\n[AMEND] Attempting connection (retries left: %d)" retries) in
  if retries <= 0 then 
    Lwt.fail (Failure "[AMEND] Out of retries for connection")
  else
    let url = Uri.of_string "wss://ws-auth.kraken.com/v2" in
    let ctx = Lazy.force Conduit_lwt_unix.default_ctx in
    Lwt.catch
      (fun () ->
        let* () = debug_log "[AMEND] Connecting..." in
        let* conn = Websocket_lwt_unix.connect ~ctx 
          (`TLS (`Hostname "ws-auth.kraken.com", 
                `IP (Ipaddr.of_string_exn "104.16.248.94"), 
                `Port 443)) url in
        
        let amend_conn = create_order_connection conn token in
        
        (* Subscribe to heartbeat *)
        let heartbeat_req = `Assoc [
          "method", `String "subscribe";
          "params", `Assoc [
            "channel", `String "heartbeat";
            "token", `String token;
          ];
          "req_id", `Int 999
        ] in
        let frame = Frame.create ~content:(Yojson.Safe.to_string heartbeat_req) () in
        let* () = Websocket_lwt_unix.write conn frame in
        let* () = debug_log "[AMEND] Subscribed to heartbeat" in
        
        (* Start background read loop *)
        Lwt.async (fun () -> read_order_connection_frames amend_conn);
        
        Lwt.return amend_conn)
      (fun e ->
        let* () = debug_log (Printf.sprintf "[AMEND] Connection error: %s" (Exn.to_string e)) in
        let* () = Lwt_unix.sleep 2.0 in
        connect_dedicated_order_connection token (retries - 1))

let round_to_precision value precision =
  let factor = 10.0 ** (Float.of_int precision) in
  Float.round (value *. factor) /. factor

let amend_order amend_conn symbol order_id current_price grid_interval price_precision =
  (* Find the order quantity from tracked pairs *)
  let order_qty =
    match List.find tracked_pairs ~f:(fun (pair, _, _, _, _) -> String.equal pair symbol) with
    | Some (_, _, qty, _, _) -> qty
    | None -> raise (Invalid_argument (Printf.sprintf "Symbol %s not found in tracked pairs" symbol))
  in

  (* Calculate new limit price with correct precision *)
  let new_limit_price = 
    round_to_precision
      (current_price *. (1.0 -. (grid_interval /. 100.0)))
      price_precision in
  
  (* Create request *)
  let req_id = Random.int 10000 + 1 in (* Avoid 0 and 999 (heartbeat) *)
  let amend_request = `Assoc [
    "method", `String "amend_order";
    "params", `Assoc [
      "order_id", `String order_id;
      "limit_price", `Float new_limit_price;
      "order_qty", `Float order_qty;
      "post_only", `Bool true;
      "token", `String amend_conn.token;
    ];
    "req_id", `Int req_id;
  ] in
  
  let amend_request_str = Yojson.Safe.to_string amend_request in
  let* () = debug_log (Printf.sprintf "[AMEND] Preparing request for %s: New limit price %.8f (%.2f%% below %.8f)" 
    order_id new_limit_price grid_interval current_price) in
  
  (* Create a promise for the response *)
  let response_promise, response_resolver = Lwt.wait () in
  Hashtbl.set amend_conn.response_promises ~key:req_id ~data:response_resolver;
  
  (* Send request *)
  let frame = Frame.create ~content:amend_request_str () in
  let* () = Websocket_lwt_unix.write amend_conn.conn frame in
  let* () = debug_log "[AMEND] Request sent" in
  
  (* Wait for response with timeout *)
  let* response = Lwt.pick [
    response_promise;
    (let* () = Lwt_unix.sleep 5.0 in
     Hashtbl.remove amend_conn.response_promises req_id;
     Lwt.return { success = false; error = Some "Timeout"; result = None })
  ] in
  
  (* Process response *)
  if response.success then
    match response.result with
    | Some result ->
        let amend_id = safe_string result "amend_id" "unknown" in
        let amended_order_id = safe_string result "order_id" order_id in
        debug_log (Printf.sprintf "[AMEND] Success - amend_id: %s, order_id: %s" 
          amend_id amended_order_id)
    | None ->
        debug_log "[AMEND] Success but no result data"
  else
    debug_log (Printf.sprintf "[AMEND] Failed - %s" 
      (Option.value response.error ~default:"Unknown error"))


let place_orders amend_conn symbol current_price =
  (* Get the configuration parameters for this symbol *)
  let (_, grid_interval, order_qty, sell_multiplier, price_precision) = 
    match List.find tracked_pairs ~f:(fun (pair, _, _, _, _) -> String.equal pair symbol) with
    | Some params -> params
    | None -> 
        raise (Invalid_argument (Printf.sprintf "Symbol %s not found in tracked pairs" symbol))
  in
  
  (* Check if we already have a pending sell order *)
  let existing_sell_order = 
    Hashtbl.to_alist open_buy_orders
    |> List.find ~f:(fun (_, order) -> String.equal order.order_symbol symbol)
  in
  
  match existing_sell_order with
  | Some _ -> 
      (* Already have a sell order, don't place another one *)
      let* () = debug_log (Printf.sprintf "[ORDER PLACE] Skipping %s - sell order already exists" symbol) in
      Lwt.return_unit
  | None ->
      let sell_qty = order_qty *. sell_multiplier in
      
      (* Use price_precision from tracked_pairs *)
      let sell_limit_price = 
        round_to_precision
          (current_price *. (1.0 +. (grid_interval /. 100.0)))
          price_precision in
      let buy_limit_price = 
        round_to_precision
          (current_price *. (1.0 -. (grid_interval /. 100.0)))
          price_precision in
      
      (* Create sell order request *)
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
          "token", `String amend_conn.token;
        ];
        "req_id", `Int sell_req_id;
      ] in

      (* Create promise for sell order response *)
      let sell_promise, sell_resolver = Lwt.wait () in
      Hashtbl.set amend_conn.response_promises ~key:sell_req_id ~data:sell_resolver;
      
      (* Send sell order and wait for response *)
      let* () = debug_log (Printf.sprintf "[ORDER PLACE] Sending SELL order for %s: %.8f @ %.8f" 
        symbol sell_qty sell_limit_price) in
      let sell_frame = Frame.create ~content:(Yojson.Safe.to_string sell_request) () in
      let* () = Websocket_lwt_unix.write amend_conn.conn sell_frame in
      
      (* Wait for sell order response *)
      let* sell_response = Lwt.pick [
        sell_promise;
        (let* () = Lwt_unix.sleep 5.0 in
         Hashtbl.remove amend_conn.response_promises sell_req_id;
         Lwt.return { success = false; error = Some "Timeout"; result = None })
      ] in
      
      (* Log sell order response *)
      let* () = match sell_response with
        | { success = true; result = Some result; _ } ->
            let order_id = safe_string result "order_id" "unknown" in
            debug_log (Printf.sprintf "[ORDER PLACE] SELL order placed successfully: %s" order_id)
        | { error = Some err; _ } ->
            debug_log (Printf.sprintf "[ORDER PLACE] SELL order failed: %s" err)
        | _ ->
            debug_log "[ORDER PLACE] SELL order failed with unknown error"
      in
      
      (* Create buy order request *)
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
          "token", `String amend_conn.token;
        ];
        "req_id", `Int buy_req_id;
      ] in
      
      (* Create promise for buy order response *)
      let buy_promise, buy_resolver = Lwt.wait () in
      Hashtbl.set amend_conn.response_promises ~key:buy_req_id ~data:buy_resolver;
      
      (* Send buy order and wait for response *)
      let* () = debug_log (Printf.sprintf "[ORDER PLACE] Sending BUY order for %s: %.8f @ %.8f" 
        symbol order_qty buy_limit_price) in
      let buy_frame = Frame.create ~content:(Yojson.Safe.to_string buy_request) () in
      let* () = Websocket_lwt_unix.write amend_conn.conn buy_frame in
      
      (* Wait for buy order response *)
      let* buy_response = Lwt.pick [
        buy_promise;
        (let* () = Lwt_unix.sleep 5.0 in
         Hashtbl.remove amend_conn.response_promises buy_req_id;
         Lwt.return { success = false; error = Some "Timeout"; result = None })
      ] in
      
      (* Log buy order response *)
      match buy_response with
      | { success = true; result = Some result; _ } ->
          let order_id = safe_string result "order_id" "unknown" in
          debug_log (Printf.sprintf "[ORDER PLACE] BUY order placed successfully: %s" order_id)
      | { error = Some err; _ } ->
          debug_log (Printf.sprintf "[ORDER PLACE] BUY order failed: %s" err)
      | _ ->
          debug_log "[ORDER PLACE] BUY order failed with unknown error"



let check_order_validity amend_conn symbol =
  (* Get current price from ticker cache *)
  let current_price = 
    match Hashtbl.find ticker_cache symbol with
    | Some ticker -> ticker.current_price
    | None -> 
        raise (Invalid_argument (Printf.sprintf "No price data for symbol %s" symbol))
  in

  (* Find the grid interval for this symbol *)
  let (_, grid_interval, _, _, _) = 
    match List.find tracked_pairs ~f:(fun (pair, _, _, _, _) -> String.equal pair symbol) with
    | Some params -> params
    | None -> 
        raise (Invalid_argument (Printf.sprintf "Symbol %s not found in tracked pairs" symbol))
  in
  
  (* Find any existing order for this symbol *)
  let existing_order = 
    Hashtbl.to_alist open_buy_orders
    |> List.find ~f:(fun (_, order) -> String.equal order.order_symbol symbol)
    |> Option.map ~f:snd
  in
  
  match existing_order with
  | Some order ->
      let price_diff_pct = 
        Float.abs ((order.limit_price -. current_price) /. current_price *. 100.0) in
      let validity_status = {
        status = if Float.(price_diff_pct <= (2.0 *. grid_interval)) then `Valid else `Invalid;
        current_price;
        price_diff_pct;
        order_id = Some order.order_id;
      } in
      
      let* () = debug_log (Printf.sprintf "[ORDER CHECK] %s - %s\n  Current Price: %.8f\n  Limit Price:  %.8f\n  Diff: %.2f%% (max %.2f%%)" 
        symbol
        (match validity_status.status with 
         | `Valid -> "VALID" 
         | `Invalid -> "INVALID"
         | `None -> "NONE")
        current_price
        order.limit_price
        validity_status.price_diff_pct
        (2.0 *. grid_interval)) in

      (match validity_status.status with
      | `Invalid ->
          (match validity_status.order_id with
          | Some order_id ->
              let price_precision = getprice_precision symbol in
              let* () = debug_log (Printf.sprintf "[ORDER AMEND] Amending order %s (current price: %.8f)" 
                order_id validity_status.current_price) in
              amend_order amend_conn symbol order_id validity_status.current_price grid_interval price_precision
          | None -> 
              let* () = debug_log "[ORDER AMEND] Error: No order ID available for amendment" in
              Lwt.return_unit)
      | `Valid ->
          Lwt.return_unit
      | `None -> Lwt.return_unit)
      
  | None ->
      let validity_status = {
        status = `None;
        current_price;
        price_diff_pct = 0.0;
        order_id = None;
      } in
      let* () = debug_log (Printf.sprintf "[ORDER VALIDITY] %s - No Order Found (current price: %.8f)" 
        symbol validity_status.current_price) in
      (* Place new orders when no existing order is found *)
      place_orders amend_conn symbol current_price

let rec read_private_frames conn =
  let* () = debug_log "[PRIVATE] Waiting for next private frame..." in
  Lwt.catch
    (fun () ->
      let* frame = Websocket_lwt_unix.read conn in
      let message = frame.content in
      let* () = debug_log (Printf.sprintf "[PRIVATE] Received message: %s" message) in
      try
        let json = Yojson.Safe.from_string message in
        let* () = parse_execution_message json in
        read_private_frames conn
      with e ->
        let* () = debug_log (Printf.sprintf "[PRIVATE] Error processing message: %s" (Exn.to_string e)) in
        read_private_frames conn)
    (fun e ->
      match e with
      | End_of_file ->
          debug_log "[PRIVATE] Connection closed, stopping read_private_frames." >>= fun () ->
          Lwt.return_unit
      | _ ->
          debug_log (Printf.sprintf "[PRIVATE] Error reading frame: %s" (Exn.to_string e)) >>= fun () ->
          read_private_frames conn)


let rec read_frames public_conn private_conn amend_conn token =
  let* () = debug_log "Waiting for next frame..." in
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
          read_frames public_conn private_conn amend_conn token
      | Some json ->
          let channel_opt = json |> member "channel" |> to_string_option in
          let event_opt = json |> member "event" |> to_string_option in
          
          (match (event_opt, channel_opt) with
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
                      
                      (* Check order validity whenever we get a new price *)
                      let* () = check_order_validity amend_conn symbol in
                      read_frames public_conn private_conn amend_conn token
                    ) else (
                      let* () = debug_log (Printf.sprintf "[TICKER] Ignoring untracked symbol: %s" symbol) in
                      read_frames public_conn private_conn amend_conn token
                    )
                | _ -> read_frames public_conn private_conn amend_conn token
              with e -> 
                let* () = debug_log (Printf.sprintf "[TICKER] Error processing ticker: %s" 
                  (Exn.to_string e)) in
                read_frames public_conn private_conn amend_conn token)
          | (_, Some "instrument") ->
              let data_type = json |> member "type" |> to_string_option in
              let* () = debug_log (Printf.sprintf "Instrument message type: %s" 
                (Option.value data_type ~default:"none")) in
              begin match data_type with
              | Some "snapshot" ->
                  let* () = debug_log "Processing instrument snapshot..." in
                  parse_instrument_data json >>= fun tracked ->
                  let* () = debug_log (Printf.sprintf "Processed %d tracked pairs" (List.length tracked)) in
                  (* After getting snapshot, unsubscribe from instrument and subscribe to ticker *)
                  let unsub_frame = Frame.create ~content:(instrument_unsubscribe_message ()) () in
                  let* () = debug_log "Unsubscribing from instrument channel..." in
                  Websocket_lwt_unix.write public_conn unsub_frame >>= fun () ->
                  let sub_ticker_frame = Frame.create ~content:(ticker_subscribe_message tracked_pairs) () in
                  let* () = debug_log "Subscribing to ticker channel..." in
                  Websocket_lwt_unix.write public_conn sub_ticker_frame >>= fun () ->
                  read_frames public_conn private_conn amend_conn token
              | _ -> 
                  let* () = debug_log "Skipping non-snapshot instrument message" in
                  read_frames public_conn private_conn amend_conn token
              end
          | (_, Some "executions") ->
              let* () = debug_log "[PRIVATE] Processing execution message" in
              let* () = debug_log (Printf.sprintf "[PRIVATE] Raw message: %s" message) in
              parse_execution_message json >>= fun () ->
              read_frames public_conn private_conn amend_conn token
          | (_, Some "status") -> 
              let* () = debug_log "Received status message" in
              read_frames public_conn private_conn amend_conn token
          | (_, Some "heartbeat") -> 
              let* () = debug_log "Received heartbeat" in
              read_frames public_conn private_conn amend_conn token
          | _ -> 
              let* () = debug_log "Received unknown message type" in
              read_frames public_conn private_conn amend_conn token)
    )
    (fun e ->
      debug_log (Printf.sprintf "Error in read_frames: %s" (Exn.to_string e)) >>= fun () ->
      read_frames public_conn private_conn amend_conn token)

let rec connect_public private_conn amend_conn token retries =
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
        read_frames public_conn private_conn amend_conn token)
      (fun e ->
        debug_log (Printf.sprintf "Connection error: %s" (Exn.to_string e)) >>= fun () ->
        Lwt_unix.sleep 2.0 >>= fun () ->
        connect_public private_conn amend_conn token (retries - 1))


let rec connect_private token retries =
  let* () = debug_log (Printf.sprintf "\n[PRIVATE] Attempting private connection (retries left: %d)" retries) in
  if retries <= 0 then 
    Lwt.fail (Failure "[PRIVATE] Out of retries for private connection")
  else
    let url = Uri.of_string "wss://ws-auth.kraken.com/v2" in
    let ctx = Lazy.force Conduit_lwt_unix.default_ctx in
    Lwt.catch
      (fun () ->
        let* () = debug_log "[PRIVATE] Connecting to private WebSocket..." in
        let* private_conn = Websocket_lwt_unix.connect ~ctx 
          (`TLS (`Hostname "ws-auth.kraken.com", 
                `IP (Ipaddr.of_string_exn "104.16.248.94"), 
                `Port 443)) url in
        let* () = debug_log "[PRIVATE] Connected successfully to private feed" in
        let sub_msg = private_subscribe_message token in
        let* () = debug_log (Printf.sprintf "[PRIVATE] Sending private subscribe message: %s" sub_msg) in
        let frame = Frame.create ~content:sub_msg () in
        let* () = Websocket_lwt_unix.write private_conn frame in
        let* () = debug_log "[PRIVATE] Subscription message sent" in
        
        Lwt.return private_conn)
      (fun e ->
        debug_log (Printf.sprintf "Connection error: %s" (Exn.to_string e)) >>= fun () ->
        Lwt_unix.sleep 2.0 >>= fun () ->
        connect_private token (retries - 1))

(*==================================
MAIN FUNCTION
===================================*)

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
          
          (* Create both private and amend connections *)
          let* private_conn = connect_private ws_token.token 3 in
          let* amend_conn = connect_dedicated_order_connection ws_token.token 3 in
          
        Lwt.join [
          connect_public private_conn amend_conn ws_token.token 3;
          read_private_frames private_conn
        ])
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