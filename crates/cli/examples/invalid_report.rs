use std::io::{self, Read};

fn main() {
    // Drain input before emitting malformed evidence to avoid a broken-pipe race.
    let mut input = Vec::new();
    io::stdin().read_to_end(&mut input).unwrap();
    println!("{{\"private\":\"synthetic private canary\"}}");
}
