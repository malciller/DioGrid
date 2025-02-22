# Start from Ubuntu
FROM ubuntu:22.04

# Install system dependencies and OPAM
RUN apt update && apt install -y \
    sudo \
    m4 \
    pkg-config \
    libffi-dev \
    libgmp-dev \
    libpcre3-dev \
    libssl-dev \
    zlib1g-dev \
    make \
    g++ \
    git \
    curl \
    opam

# Install OCaml and set up OPAM environment
RUN opam init --disable-sandboxing -y && \
    opam repository set-url default https://opam.ocaml.org && \
    opam update && \
    opam switch create 4.14.0 && \
    eval $(opam env) && \
    opam pin add dune https://github.com/ocaml/dune.git#3.17.0 -y && \
    opam install -y \
    core \
    core_unix \
    cohttp-lwt-unix \
    lwt \
    yojson \
    websocket-lwt-unix \
    digestif \
    base64 \
    dotenv \
    tls \
    tls-lwt \
    conduit-lwt \
    conduit-lwt-unix \
    lwt_ssl

# Set the working directory
WORKDIR /app

# Copy the project files
COPY . .

# Change ownership to the default user
RUN chown -R root:root /app

# Build the project
RUN eval $(opam env) && dune build && \
    cp _build/default/bin/main.exe /usr/local/bin/main

# Ensure OPAM environment is loaded when running the container
ENV PATH="/root/.opam/4.14.0/bin:$PATH"

# Run the application directly
CMD ["main"]
