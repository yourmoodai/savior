//! Transport adapters never own detection or replacement semantics.
pub mod adapter;
pub mod certificate;
pub mod gateway;
pub mod policy;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum Error {
    #[error("invalid_network_policy")]
    Policy,
    #[error("unsupported_request")]
    Unsupported,
    #[error("ambiguous_prompt")]
    Ambiguous,
    #[error("invalid_request")]
    Request,
    #[error("request_too_large")]
    TooLarge,
    #[error("processing_failed")]
    Processing,
    #[error("processing_busy")]
    Busy,
    #[error("network_timeout")]
    Timeout,
    #[error("upstream_unavailable")]
    Upstream,
    #[error("certificate_unavailable")]
    Certificate,
    #[error("network_io_failed")]
    Io,
}

pub type Result<T> = std::result::Result<T, Error>;
