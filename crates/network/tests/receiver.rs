use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Request, Response, body::Incoming, service::service_fn};
use hyper_util::rt::{TokioExecutor, TokioIo};
use savior::{Detector, ReplacementMode, Span};
use savior_network::{
    Error, Result,
    certificate::Authority,
    gateway::{Gateway, RewriteWorker, Transformer},
    policy::{Destination, Format, Policy, Submission},
};
use std::{convert::Infallible, io::Write, net::SocketAddr, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::mpsc,
    task::JoinHandle,
    time::timeout,
};
use tokio_rustls::{TlsAcceptor, TlsConnector};

struct FixtureBody {
    sent: bool,
    trailing: Option<std::pin::Pin<Box<tokio::time::Sleep>>>,
}

impl hyper::body::Body for FixtureBody {
    type Data = Bytes;
    type Error = Infallible;
    fn poll_frame(
        self: std::pin::Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<std::result::Result<hyper::body::Frame<Bytes>, Infallible>>> {
        let this = self.get_mut();
        if !this.sent {
            this.sent = true;
            return std::task::Poll::Ready(Some(Ok(hyper::body::Frame::data(Bytes::from_static(
                b"data: accepted\n\n",
            )))));
        }
        if let Some(delay) = &mut this.trailing {
            if std::future::Future::poll(delay.as_mut(), context).is_pending() {
                return std::task::Poll::Pending;
            }
            this.trailing = None;
            return std::task::Poll::Ready(Some(Ok(hyper::body::Frame::data(Bytes::from_static(
                b"data: done\n\n",
            )))));
        }
        std::task::Poll::Ready(None)
    }
}

struct SyntheticDetector;
impl Detector for SyntheticDetector {
    fn detect(&mut self, text: &str) -> savior::Result<Vec<Span>> {
        Ok(text
            .match_indices("idiot")
            .map(|(start, value)| Span {
                start,
                end: start + value.len(),
                score: 1.0,
            })
            .collect())
    }
}

struct FailingWorker;
impl Transformer for FailingWorker {
    fn transform(&self, _: &[u8], _: Format) -> Result<savior_network::adapter::Transformed> {
        Err(Error::Processing)
    }
}

struct Harness {
    proxy: SocketAddr,
    native_proxy: SocketAddr,
    upstream: SocketAddr,
    client_tls: Arc<rustls::ClientConfig>,
    received: mpsc::UnboundedReceiver<(Vec<u8>, hyper::HeaderMap)>,
    tasks: Vec<JoinHandle<()>>,
}

impl Drop for Harness {
    fn drop(&mut self) {
        for task in &self.tasks {
            task.abort();
        }
    }
}

impl Harness {
    async fn new(h2_upstream: bool, fail: bool) -> Self {
        let worker: Arc<dyn Transformer> = if fail {
            Arc::new(FailingWorker)
        } else {
            Arc::new(
                RewriteWorker::new(
                    SyntheticDetector,
                    0.5,
                    ReplacementMode::new(Some("fixed_phrase"), Some("colleague".into())).unwrap(),
                )
                .unwrap(),
            )
        };
        Self::with_worker(h2_upstream, worker, true).await
    }

    async fn with_worker(
        h2_upstream: bool,
        worker: Arc<dyn Transformer>,
        trust_upstream: bool,
    ) -> Self {
        Self::with_streaming(h2_upstream, worker, trust_upstream, false).await
    }

    async fn with_streaming(
        h2_upstream: bool,
        worker: Arc<dyn Transformer>,
        trust_upstream: bool,
        streaming: bool,
    ) -> Self {
        let (upstream_ca, _) = Authority::generate().unwrap();
        let mut upstream_config = upstream_ca.server_config("localhost").unwrap();
        if !h2_upstream {
            Arc::get_mut(&mut upstream_config).unwrap().alpn_protocols = vec![b"http/1.1".to_vec()];
        }
        let upstream_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let upstream = upstream_listener.local_addr().unwrap();
        let (sent, received) = mpsc::unbounded_channel();
        let server = tokio::spawn(async move {
            loop {
                let (stream, _) = upstream_listener.accept().await.unwrap();
                let acceptor = TlsAcceptor::from(upstream_config.clone());
                let sent = sent.clone();
                tokio::spawn(async move {
                    let Ok(stream) = acceptor.accept(stream).await else {
                        return;
                    };
                    let service = service_fn(move |request: Request<Incoming>| {
                        let sent = sent.clone();
                        async move {
                            let (parts, body) = request.into_parts();
                            let body = body.collect().await.unwrap().to_bytes();
                            sent.send((body.to_vec(), parts.headers)).unwrap();
                            Ok::<_, Infallible>(Response::new(FixtureBody {
                                sent: false,
                                trailing: streaming
                                    .then(|| Box::pin(tokio::time::sleep(Duration::from_secs(60)))),
                            }))
                        }
                    });
                    if h2_upstream {
                        let _ = hyper::server::conn::http2::Builder::new(TokioExecutor::new())
                            .serve_connection(TokioIo::new(stream), service)
                            .await;
                    } else {
                        let _ = hyper::server::conn::http1::Builder::new()
                            .serve_connection(TokioIo::new(stream), service)
                            .await;
                    }
                });
            }
        });
        let (proxy_ca, _) = Authority::generate().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let proxy = listener.local_addr().unwrap();
        let native_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let native_proxy = native_listener.local_addr().unwrap();
        let policy = Policy {
            version: 1,
            listen: proxy,
            native_listen: Some(native_proxy),
            destinations: vec![Destination {
                host: "localhost".into(),
                port: upstream.port(),
                submissions: vec![Submission {
                    path: "/v1/responses".into(),
                    format: Format::OpenaiResponses,
                }],
            }],
            max_body_bytes: 4096,
            timeout_ms: 2000,
            max_connections: 16,
        }; // 4 KiB fixture request limit
        let mut roots = rustls::RootCertStore::empty();
        if trust_upstream {
            roots.add(upstream_ca.certificate()).unwrap();
        }
        let gateway = Gateway::new(policy, &proxy_ca, worker, roots).unwrap();
        let native_gateway = gateway.clone();
        let native_config = proxy_ca.server_config("localhost").unwrap();
        let native_task = tokio::spawn(async move {
            native_gateway
                .serve_local_tls(native_listener, native_config)
                .await
                .unwrap();
        });
        let proxy_task = tokio::spawn(async move {
            gateway.serve(listener).await.unwrap();
        });
        let mut roots = rustls::RootCertStore::empty();
        roots.add(proxy_ca.certificate()).unwrap();
        let config = rustls::ClientConfig::builder_with_provider(Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(roots)
        .with_no_client_auth();
        Self {
            proxy,
            native_proxy,
            upstream,
            client_tls: Arc::new(config),
            received,
            tasks: vec![server, proxy_task, native_task],
        }
    }

    async fn request(
        &self,
        body: Vec<u8>,
        encoding: Option<&str>,
        h2_client: bool,
        path: &str,
    ) -> (hyper::StatusCode, Vec<u8>) {
        self.request_via(body, encoding, h2_client, path, false)
            .await
    }

    async fn request_via(
        &self,
        body: Vec<u8>,
        encoding: Option<&str>,
        h2_client: bool,
        path: &str,
        native: bool,
    ) -> (hyper::StatusCode, Vec<u8>) {
        let response = self.send_via(body, encoding, h2_client, path, native).await;
        let status = response.status();
        (
            status,
            response
                .into_body()
                .collect()
                .await
                .unwrap()
                .to_bytes()
                .to_vec(),
        )
    }

    async fn send_via(
        &self,
        body: Vec<u8>,
        encoding: Option<&str>,
        h2_client: bool,
        path: &str,
        native: bool,
    ) -> Response<Incoming> {
        trait Transport: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send {}
        impl<T: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send> Transport for T {}
        let mut stream: Box<dyn Transport> = if native {
            let mut config = (*self.client_tls).clone();
            config.alpn_protocols = vec![b"http/1.1".to_vec()];
            let stream = TcpStream::connect(self.native_proxy).await.unwrap();
            Box::new(
                TlsConnector::from(Arc::new(config))
                    .connect("localhost".try_into().unwrap(), stream)
                    .await
                    .unwrap(),
            )
        } else {
            Box::new(TcpStream::connect(self.proxy).await.unwrap())
        };
        stream
            .write_all(
                format!(
                    "CONNECT localhost:{} HTTP/1.1\r\nHost: localhost:{}\r\n\r\n",
                    self.upstream.port(),
                    self.upstream.port()
                )
                .as_bytes(),
            )
            .await
            .unwrap();
        let mut header = Vec::new();
        while !header.ends_with(b"\r\n\r\n") {
            header.push(stream.read_u8().await.unwrap());
        }
        assert!(header.starts_with(b"HTTP/1.1 200"));
        let mut config = (*self.client_tls).clone();
        config.alpn_protocols = vec![if h2_client {
            b"h2".to_vec()
        } else {
            b"http/1.1".to_vec()
        }];
        let stream = TlsConnector::from(Arc::new(config))
            .connect("localhost".try_into().unwrap(), stream)
            .await
            .unwrap();
        let mut builder = Request::builder()
            .method("POST")
            .uri(format!(
                "https://localhost:{}{}",
                self.upstream.port(),
                path
            ))
            .header("content-type", "application/json")
            .header("authorization", "Bearer synthetic")
            .header("x-fixture", "untouched");
        if let Some(encoding) = encoding {
            builder = builder.header("content-encoding", encoding);
        }
        let request = builder.body(Full::new(Bytes::from(body))).unwrap();
        if h2_client {
            let (mut sender, connection) =
                hyper::client::conn::http2::handshake(TokioExecutor::new(), TokioIo::new(stream))
                    .await
                    .unwrap();
            tokio::spawn(async move {
                let _ = connection.await;
            });
            sender.send_request(request).await.unwrap()
        } else {
            let (mut sender, connection) =
                hyper::client::conn::http1::handshake(TokioIo::new(stream))
                    .await
                    .unwrap();
            tokio::spawn(async move {
                let _ = connection.await;
            });
            sender.send_request(request).await.unwrap()
        }
    }

    async fn no_delivery(&mut self) {
        assert!(
            timeout(Duration::from_millis(100), self.received.recv())
                .await
                .is_err()
        );
    }
}

#[tokio::test]
async fn receiver_observes_only_the_rewritten_string_across_http_versions() {
    for h2 in [false, true] {
        let mut harness = Harness::new(h2, false).await;
        let original = r#"{ "input": [{"role":"assistant","content":"idiot"},{"role":"user","content":"Hello idiot, keep \"this\"."}], "metadata":{"n":1.0000,"text":"idiot"}, "stream":true }"#;
        let expected = original.replace("Hello idiot", "Hello colleague");
        let (status, response) = harness
            .request(original.as_bytes().to_vec(), None, h2, "/v1/responses")
            .await;
        assert_eq!(status, 200);
        assert_eq!(response, b"data: accepted\n\n");
        let (delivered, headers) = timeout(Duration::from_secs(2), harness.received.recv())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(delivered, expected.as_bytes());
        assert_eq!(headers["authorization"], "Bearer synthetic");
        assert_eq!(headers["x-fixture"], "untouched");
    }
}

#[tokio::test]
async fn compressed_input_is_rewritten_before_delivery() {
    let mut harness = Harness::new(true, false).await;
    let mut gzip = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
    gzip.write_all(br#"{"input":"idiot"}"#).unwrap();
    assert_eq!(
        harness
            .request(gzip.finish().unwrap(), Some("gzip"), false, "/v1/responses")
            .await
            .0,
        200
    );
    let (body, headers) = harness.received.recv().await.unwrap();
    assert_eq!(body, br#"{"input":"colleague"}"#);
    assert!(!headers.contains_key("content-encoding"));
}

#[tokio::test]
async fn refusal_paths_deliver_nothing_to_the_receiver() {
    let mut harness = Harness::new(true, false).await;
    for (body, encoding, path) in [
        (br#"{"input":"idiot","input":"harmless"}"#.to_vec(), None, "/v1/responses"),
        (br#"{"input":"`idiot`"}"#.to_vec(), None, "/v1/responses"),
        (br#"{"input":[{"role":"user","content":[{"type":"input_text","text":"idiot"},{"type":"input_text","text":"file"}]}]}"#.to_vec(), None, "/v1/responses"),
        (br#"{"input":"idiot"}"#.to_vec(), Some("br"), "/v1/responses"),
        (br#"{"input":"idiot"}"#.to_vec(), None, "/unknown"),
        (vec![b'x'; 4097], None, "/v1/responses"),
    ] {
        assert!(!harness.request(body, encoding, false, path).await.0.is_success());
        harness.no_delivery().await;
    }
    let mut failed = Harness::new(true, true).await;
    assert_eq!(
        failed
            .request(
                br#"{"input":"idiot"}"#.to_vec(),
                None,
                false,
                "/v1/responses"
            )
            .await
            .0,
        403
    );
    failed.no_delivery().await;
}

#[tokio::test]
async fn an_untrusted_upstream_receives_no_prompt() {
    let worker =
        Arc::new(RewriteWorker::new(SyntheticDetector, 0.5, ReplacementMode::default()).unwrap());
    let mut harness = Harness::with_worker(true, worker, false).await;
    assert_eq!(
        harness
            .request(
                br#"{"input":"idiot"}"#.to_vec(),
                None,
                false,
                "/v1/responses"
            )
            .await
            .0,
        502
    );
    harness.no_delivery().await;
}

#[tokio::test]
async fn native_tls_hop_authenticates_the_local_gateway_before_relaying_client_tls() {
    let mut harness = Harness::new(true, false).await;
    assert_eq!(
        harness
            .request_via(
                br#"{"input":"idiot"}"#.to_vec(),
                None,
                true,
                "/v1/responses",
                true
            )
            .await
            .0,
        200
    );
    assert_eq!(
        harness.received.recv().await.unwrap().0,
        br#"{"input":"colleague"}"#
    );
    let config = rustls::ClientConfig::builder_with_provider(Arc::new(
        rustls::crypto::ring::default_provider(),
    ))
    .with_safe_default_protocol_versions()
    .unwrap()
    .with_root_certificates(rustls::RootCertStore::empty())
    .with_no_client_auth();
    let stream = TcpStream::connect(harness.native_proxy).await.unwrap();
    assert!(
        TlsConnector::from(Arc::new(config))
            .connect("localhost".try_into().unwrap(), stream)
            .await
            .is_err()
    );
    harness.no_delivery().await;
}

#[tokio::test]
async fn the_first_sse_frame_does_not_wait_for_the_rest_of_the_response() {
    let worker =
        Arc::new(RewriteWorker::new(SyntheticDetector, 0.5, ReplacementMode::default()).unwrap());
    let mut harness = Harness::with_streaming(true, worker, true, true).await;
    let response = timeout(
        Duration::from_secs(2),
        harness.send_via(
            br#"{"input":"idiot"}"#.to_vec(),
            None,
            true,
            "/v1/responses",
            false,
        ),
    )
    .await
    .unwrap();
    assert_eq!(response.status(), 200);
    let mut body = response.into_body();
    let frame = timeout(Duration::from_secs(2), body.frame())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(frame.data_ref().unwrap(), b"data: accepted\n\n".as_slice());
    assert!(
        timeout(Duration::from_millis(50), body.frame())
            .await
            .is_err()
    );
    let delivered = harness.received.recv().await.unwrap().0;
    assert!(!String::from_utf8(delivered).unwrap().contains("idiot"));
}

#[tokio::test]
#[ignore = "requires explicitly configured SAVIOR_MODEL_DIR; never downloads artifacts"]
async fn installed_offline_detector_delivers_a_rewritten_prompt() {
    let directory = std::env::var("SAVIOR_MODEL_DIR").expect("model directory is required");
    let detector = savior::RuntimeDetector::load(
        directory,
        "bert",
        savior::Policy::OffensiveSpans,
        Default::default(),
    )
    .unwrap();
    let threshold = detector.threshold();
    let worker = Arc::new(
        RewriteWorker::new(
            detector,
            threshold,
            ReplacementMode::new(Some("fixed_phrase"), Some("colleague".into())).unwrap(),
        )
        .unwrap(),
    );
    let mut harness = Harness::with_worker(true, worker, true).await;
    let original =
        br#"{"input":"You idiot, do not delete the test.", "metadata":{"history":"idiot"}}"#;
    assert_eq!(
        harness
            .request(original.to_vec(), None, true, "/v1/responses")
            .await
            .0,
        200
    );
    let (body, _) = harness.received.recv().await.unwrap();
    let delivered: serde_json::Value = serde_json::from_slice(&body).unwrap();
    let prompt = delivered["input"].as_str().unwrap();
    assert!(!prompt.contains("idiot"));
    assert!(prompt.ends_with("do not delete the test."));
    assert!(prompt.contains("colleague"));
    assert_eq!(delivered["metadata"]["history"], "idiot");
}
