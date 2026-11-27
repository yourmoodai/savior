use crate::{
    Error, Result, adapter,
    certificate::Authority,
    policy::{Destination, Policy},
};
use bytes::Bytes;
use http_body_util::{BodyExt, Full, Limited, combinators::UnsyncBoxBody};
use hyper::{
    Method, Request, Response, StatusCode, Uri,
    body::Incoming,
    header::{self, HeaderMap, HeaderValue},
    service::service_fn,
};
use hyper_util::rt::{TokioExecutor, TokioIo};
use savior::{Detector, ReplacementMode};
use std::{
    collections::BTreeMap,
    convert::Infallible,
    io::Read,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    net::{TcpListener, TcpStream},
    sync::Semaphore,
    time::timeout,
};
use tokio_rustls::{TlsAcceptor, TlsConnector};

type Body = UnsyncBoxBody<Bytes, Error>;
trait Transport: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send {}
impl<T: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send> Transport for T {}

struct AdmittedBody {
    inner: Body,
    _permit: Arc<tokio::sync::OwnedSemaphorePermit>,
}

impl hyper::body::Body for AdmittedBody {
    type Data = Bytes;
    type Error = Error;
    fn poll_frame(
        self: std::pin::Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Result<hyper::body::Frame<Bytes>>>> {
        std::pin::Pin::new(&mut self.get_mut().inner).poll_frame(context)
    }
    fn is_end_stream(&self) -> bool {
        self.inner.is_end_stream()
    }
    fn size_hint(&self) -> hyper::body::SizeHint {
        self.inner.size_hint()
    }
}

pub trait Transformer: Send + Sync + 'static {
    fn transform(&self, body: &[u8], format: crate::policy::Format)
    -> Result<adapter::Transformed>;
}

pub struct RewriteWorker<D> {
    detector: Mutex<D>,
    replacement: ReplacementMode,
    threshold: f32,
}

impl<D: Detector + Send + 'static> RewriteWorker<D> {
    pub fn new(detector: D, threshold: f32, replacement: ReplacementMode) -> Result<Self> {
        if !threshold.is_finite() || threshold <= 0.0 || threshold > 1.0 {
            return Err(Error::Policy);
        }
        Ok(Self {
            detector: Mutex::new(detector),
            replacement,
            threshold,
        })
    }
}

impl<D: Detector + Send + 'static> Transformer for RewriteWorker<D> {
    fn transform(
        &self,
        body: &[u8],
        format: crate::policy::Format,
    ) -> Result<adapter::Transformed> {
        let mut detector = self.detector.try_lock().map_err(|_| Error::Busy)?;
        adapter::transform(
            body,
            format,
            &mut *detector,
            self.threshold,
            &self.replacement,
        )
    }
}

pub struct Gateway {
    policy: Policy,
    certificates: BTreeMap<String, Arc<rustls::ServerConfig>>,
    worker: Arc<dyn Transformer>,
    upstream_tls: Arc<rustls::ClientConfig>,
    connections: Arc<Semaphore>,
    requests: Arc<Semaphore>,
}

impl Gateway {
    pub fn new(
        policy: Policy,
        authority: &Authority,
        worker: Arc<dyn Transformer>,
        roots: rustls::RootCertStore,
    ) -> Result<Arc<Self>> {
        policy.validate()?;
        let mut certificates = BTreeMap::new();
        for destination in &policy.destinations {
            certificates.insert(
                destination.host.clone(),
                authority.server_config(&destination.host)?,
            );
        }
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let mut tls = rustls::ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(|_| Error::Certificate)?
            .with_root_certificates(roots)
            .with_no_client_auth();
        tls.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
        Ok(Arc::new(Self {
            connections: Arc::new(Semaphore::new(policy.max_connections)),
            requests: Arc::new(Semaphore::new(policy.max_connections)),
            policy,
            certificates,
            worker,
            upstream_tls: Arc::new(tls),
        }))
    }

    pub async fn serve(self: Arc<Self>, listener: TcpListener) -> Result<()> {
        if listener.local_addr().map_err(|_| Error::Io)?.ip() != self.policy.listen.ip() {
            return Err(Error::Policy);
        }
        self.serve_listener(listener, None).await
    }

    /// Native routing authenticates this outer TLS hop before relaying the client's TLS bytes.
    pub async fn serve_local_tls(
        self: Arc<Self>,
        listener: TcpListener,
        config: Arc<rustls::ServerConfig>,
    ) -> Result<()> {
        let address = listener.local_addr().map_err(|_| Error::Io)?;
        if !address.ip().is_loopback() || self.policy.native_listen != Some(address) {
            return Err(Error::Policy);
        }
        self.serve_listener(listener, Some(config)).await
    }

    async fn serve_listener(
        self: Arc<Self>,
        listener: TcpListener,
        tls: Option<Arc<rustls::ServerConfig>>,
    ) -> Result<()> {
        loop {
            let (stream, _) = listener.accept().await.map_err(|_| Error::Io)?;
            let Ok(permit) = self.connections.clone().try_acquire_owned() else {
                drop(stream);
                continue;
            };
            let gateway = self.clone();
            let tls = tls.clone();
            tokio::spawn(async move {
                let permit = Arc::new(permit);
                let stream: Box<dyn Transport> = match tls {
                    None => Box::new(stream),
                    Some(config) => match timeout(
                        Duration::from_secs(10),
                        TlsAcceptor::from(config).accept(stream),
                    )
                    .await
                    {
                        Ok(Ok(stream)) => Box::new(stream),
                        _ => return,
                    },
                };
                let service = service_fn(move |request| {
                    let gateway = gateway.clone();
                    let permit = permit.clone();
                    async move {
                        Ok::<_, Infallible>(
                            gateway
                                .connect(request, permit)
                                .await
                                .unwrap_or_else(refusal),
                        )
                    }
                });
                let connection = hyper::server::conn::http1::Builder::new()
                    .max_buf_size(32 * 1024) // 32 KiB, outer CONNECT headers
                    .serve_connection(TokioIo::new(stream), service)
                    .with_upgrades();
                let _ = timeout(Duration::from_secs(30), connection).await;
            });
        }
    }

    async fn connect(
        self: Arc<Self>,
        mut request: Request<Incoming>,
        permit: Arc<tokio::sync::OwnedSemaphorePermit>,
    ) -> Result<Response<Body>> {
        if request.method() != Method::CONNECT {
            return Err(Error::Unsupported);
        }
        let authority = request.uri().authority().ok_or(Error::Request)?;
        let host = authority.host().to_owned();
        let port = authority.port_u16().ok_or(Error::Request)?;
        let destination = self.policy.destination(&host, port)?.clone();
        if request.headers().contains_key(header::TRANSFER_ENCODING)
            || request
                .headers()
                .get(header::CONTENT_LENGTH)
                .is_some_and(|value| value != "0")
        {
            return Err(Error::Request);
        }
        let certificate = self
            .certificates
            .get(&host)
            .ok_or(Error::Certificate)?
            .clone();
        tokio::spawn(async move {
            let _permit = permit;
            let upgraded =
                match timeout(Duration::from_secs(10), hyper::upgrade::on(&mut request)).await {
                    Ok(Ok(upgraded)) => upgraded,
                    _ => return,
                };
            let stream = match timeout(
                Duration::from_secs(10),
                TlsAcceptor::from(certificate).accept(TokioIo::new(upgraded)),
            )
            .await
            {
                Ok(Ok(stream)) => stream,
                _ => return,
            };
            let service = service_fn(move |request| {
                let gateway = self.clone();
                let destination = destination.clone();
                async move {
                    let deadline = Duration::from_millis(gateway.policy.timeout_ms);
                    let response =
                        match timeout(deadline, gateway.submission(request, destination)).await {
                            Ok(Ok(response)) => response,
                            Ok(Err(error)) => refusal(error),
                            Err(_) => refusal(Error::Timeout),
                        };
                    Ok::<_, Infallible>(response)
                }
            });
            let mut builder = hyper_util::server::conn::auto::Builder::new(TokioExecutor::new());
            builder.http1().max_buf_size(64 * 1024); // 64 KiB, decrypted request headers
            builder
                .http2()
                .max_concurrent_streams(16)
                .max_header_list_size(64 * 1024); // 64 KiB
            let _ = timeout(
                Duration::from_secs(3600),
                builder.serve_connection(TokioIo::new(stream), service),
            )
            .await;
        });
        Ok(Response::new(full(Bytes::new())))
    }

    async fn submission(
        self: Arc<Self>,
        request: Request<Incoming>,
        destination: Destination,
    ) -> Result<Response<Body>> {
        let permit = Arc::new(
            self.requests
                .clone()
                .try_acquire_owned()
                .map_err(|_| Error::Busy)?,
        );
        if request.method() != Method::POST || request.uri().query().is_some() {
            return Err(Error::Unsupported);
        }
        if request.uri().authority().is_some_and(|a| {
            a.host() != destination.host || a.port_u16().unwrap_or(443) != destination.port
        }) {
            return Err(Error::Request);
        }
        if let Some(host) = request.headers().get(header::HOST) {
            let authority: hyper::http::uri::Authority = host
                .to_str()
                .map_err(|_| Error::Request)?
                .parse()
                .map_err(|_| Error::Request)?;
            if authority.host() != destination.host
                || authority.port_u16().unwrap_or(443) != destination.port
            {
                return Err(Error::Request);
            }
        }
        let format = destination
            .submissions
            .iter()
            .find(|entry| entry.path == request.uri().path())
            .ok_or(Error::Unsupported)?
            .format;
        validate_headers(request.headers())?;
        let (mut parts, body) = request.into_parts();
        let encoded = Limited::new(body, self.policy.max_body_bytes)
            .collect()
            .await
            .map_err(|_| Error::TooLarge)?
            .to_bytes();
        let encoding = parts
            .headers
            .get(header::CONTENT_ENCODING)
            .map(|value| {
                value
                    .to_str()
                    .map(str::trim)
                    .map(str::to_ascii_lowercase)
                    .map_err(|_| Error::Request)
            })
            .transpose()?
            .unwrap_or_else(|| "identity".into());
        let worker = self.worker.clone();
        let limit = self.policy.max_body_bytes;
        let worker_permit = permit.clone();
        let transformed = tokio::task::spawn_blocking(move || {
            let _permit = worker_permit;
            let decoded = decode(&encoded, &encoding, limit)?;
            let transformed = worker.transform(&decoded, format)?;
            if transformed.body.len() > limit {
                return Err(Error::TooLarge);
            }
            Ok(transformed.body)
        })
        .await
        .map_err(|_| Error::Processing)??;
        sanitize_headers(&mut parts.headers)?;
        parts.headers.remove(header::CONTENT_ENCODING);
        parts.headers.remove(header::CONTENT_LENGTH);
        parts
            .headers
            .insert(header::CONTENT_LENGTH, HeaderValue::from(transformed.len()));
        let uri: Uri = format!(
            "https://{}:{}{}",
            destination.host,
            destination.port,
            parts.uri.path()
        )
        .parse()
        .map_err(|_| Error::Request)?;
        parts.uri = uri;
        let request = Request::from_parts(parts, Full::new(Bytes::from(transformed)));
        let (parts, body) = self.forward(request, &destination).await?.into_parts();
        Ok(Response::from_parts(
            parts,
            AdmittedBody {
                inner: body,
                _permit: permit,
            }
            .boxed_unsync(),
        ))
    }

    async fn forward(
        &self,
        mut request: Request<Full<Bytes>>,
        destination: &Destination,
    ) -> Result<Response<Body>> {
        let stream = TcpStream::connect((destination.host.as_str(), destination.port))
            .await
            .map_err(|_| Error::Upstream)?;
        let name = rustls::pki_types::ServerName::try_from(destination.host.clone())
            .map_err(|_| Error::Request)?;
        let stream = TlsConnector::from(self.upstream_tls.clone())
            .connect(name, stream)
            .await
            .map_err(|_| Error::Upstream)?;
        let h2 = stream.get_ref().1.alpn_protocol() == Some(b"h2");
        let response = if h2 {
            request.extensions_mut().clear();
            let (mut sender, connection) =
                hyper::client::conn::http2::handshake(TokioExecutor::new(), TokioIo::new(stream))
                    .await
                    .map_err(|_| Error::Upstream)?;
            tokio::spawn(async move {
                let _ = connection.await;
            });
            sender
                .send_request(request)
                .await
                .map_err(|_| Error::Upstream)?
        } else {
            *request.version_mut() = hyper::Version::HTTP_11;
            let authority = request
                .uri()
                .authority()
                .ok_or(Error::Request)?
                .as_str()
                .to_owned();
            let path = request.uri().path().parse().map_err(|_| Error::Request)?;
            *request.uri_mut() = path;
            request.headers_mut().insert(
                header::HOST,
                HeaderValue::from_str(&authority).map_err(|_| Error::Request)?,
            );
            let (mut sender, connection) =
                hyper::client::conn::http1::handshake(TokioIo::new(stream))
                    .await
                    .map_err(|_| Error::Upstream)?;
            tokio::spawn(async move {
                let _ = connection.await;
            });
            sender
                .send_request(request)
                .await
                .map_err(|_| Error::Upstream)?
        };
        let (mut parts, body) = response.into_parts();
        if parts.status == StatusCode::SWITCHING_PROTOCOLS {
            return Err(Error::Unsupported);
        }
        sanitize_headers(&mut parts.headers)?;
        Ok(Response::from_parts(
            parts,
            body.map_err(|_| Error::Upstream).boxed_unsync(),
        ))
    }
}

fn full(bytes: Bytes) -> Body {
    Full::new(bytes)
        .map_err(|error: Infallible| match error {})
        .boxed_unsync()
}

fn refusal(error: Error) -> Response<Body> {
    let status = match error {
        Error::TooLarge => StatusCode::PAYLOAD_TOO_LARGE,
        Error::Busy => StatusCode::SERVICE_UNAVAILABLE,
        Error::Timeout => StatusCode::GATEWAY_TIMEOUT,
        Error::Upstream => StatusCode::BAD_GATEWAY,
        _ => StatusCode::FORBIDDEN,
    };
    let mut response = Response::new(full(Bytes::from(
        serde_json::json!({"version":1,"status":"blocked","code":error.to_string()}).to_string(),
    )));
    *response.status_mut() = status;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn validate_headers(headers: &HeaderMap) -> Result<()> {
    let content_type = headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .ok_or(Error::Request)?;
    if !content_type
        .split(';')
        .next()
        .is_some_and(|value| value.trim().eq_ignore_ascii_case("application/json"))
    {
        return Err(Error::Unsupported);
    }
    for parameter in content_type.split(';').skip(1) {
        let (name, value) = parameter.trim().split_once('=').ok_or(Error::Unsupported)?;
        if !name.trim().eq_ignore_ascii_case("charset")
            || !value.trim().trim_matches('"').eq_ignore_ascii_case("utf-8")
        {
            return Err(Error::Unsupported);
        }
    }
    for name in [
        "upgrade",
        "content-md5",
        "digest",
        "content-digest",
        "signature",
        "signature-input",
        "trailer",
        "x-amz-content-sha256",
    ] {
        if headers.contains_key(name) {
            return Err(Error::Unsupported);
        }
    }
    if headers
        .get(header::AUTHORIZATION)
        .is_some_and(|value| !value.as_bytes().starts_with(b"Bearer "))
    {
        return Err(Error::Unsupported);
    }
    if headers.get_all(header::CONTENT_ENCODING).iter().count() > 1
        || headers.get_all(header::CONTENT_TYPE).iter().count() > 1
    {
        return Err(Error::Request);
    }
    Ok(())
}

fn sanitize_headers(headers: &mut HeaderMap) -> Result<()> {
    let connection = headers
        .get_all(header::CONNECTION)
        .iter()
        .map(|value| {
            value
                .to_str()
                .map(str::to_owned)
                .map_err(|_| Error::Request)
        })
        .collect::<Result<Vec<_>>>()?;
    for value in connection {
        for token in value.split(',') {
            let name = hyper::header::HeaderName::from_bytes(token.trim().as_bytes())
                .map_err(|_| Error::Request)?;
            // A nominated application header could alter prompt interpretation or authentication.
            if !["keep-alive", "close"].contains(&name.as_str()) {
                return Err(Error::Unsupported);
            }
            headers.remove(name);
        }
    }
    for name in [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    ] {
        headers.remove(name);
    }
    Ok(())
}

fn decode(body: &[u8], encoding: &str, limit: usize) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    match encoding {
        "identity" => output.extend_from_slice(body),
        "gzip" => {
            flate2::read::MultiGzDecoder::new(body)
                .take(limit as u64 + 1)
                .read_to_end(&mut output)
                .map_err(|_| Error::Request)?;
        }
        "deflate" => {
            flate2::read::ZlibDecoder::new(body)
                .take(limit as u64 + 1)
                .read_to_end(&mut output)
                .map_err(|_| Error::Request)?;
        }
        _ => return Err(Error::Unsupported),
    }
    if output.len() > limit {
        return Err(Error::TooLarge);
    }
    Ok(output)
}
