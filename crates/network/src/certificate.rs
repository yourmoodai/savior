use crate::{Error, Result};
use rcgen::{
    BasicConstraints, CertificateParams, DnType, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
    KeyUsagePurpose,
};
use rustls::{
    ServerConfig,
    pki_types::{CertificateDer, PrivatePkcs8KeyDer},
};
use std::sync::Arc;

pub struct Authority {
    issuer: Issuer<'static, KeyPair>,
    certificate: CertificateDer<'static>,
    pem: String,
}

impl Authority {
    pub fn generate() -> Result<(Self, String)> {
        let key = KeyPair::generate().map_err(|_| Error::Certificate)?;
        let private_key = key.serialize_pem();
        let mut params = CertificateParams::default();
        params
            .distinguished_name
            .push(DnType::CommonName, "savior local prompt gateway");
        params.is_ca = IsCa::Ca(BasicConstraints::Constrained(0));
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let certificate = params.self_signed(&key).map_err(|_| Error::Certificate)?;
        let pem = certificate.pem();
        Ok((
            Self {
                issuer: Issuer::new(params, key),
                certificate: certificate.der().clone(),
                pem,
            },
            private_key,
        ))
    }

    pub fn from_pem(certificate: &str, private_key: &str) -> Result<Self> {
        use rustls::pki_types::pem::PemObject;
        let der = CertificateDer::from_pem_slice(certificate.as_bytes())
            .map_err(|_| Error::Certificate)?;
        let key = KeyPair::from_pem(private_key).map_err(|_| Error::Certificate)?;
        rustls::sign::CertifiedKey::from_der(
            vec![der.clone()],
            PrivatePkcs8KeyDer::from(key.serialize_der()).into(),
            &rustls::crypto::ring::default_provider(),
        )
        .and_then(|certified| certified.keys_match())
        .map_err(|_| Error::Certificate)?;
        let issuer = Issuer::from_ca_cert_pem(certificate, key).map_err(|_| Error::Certificate)?;
        Ok(Self {
            issuer,
            certificate: der,
            pem: certificate.to_owned(),
        })
    }

    pub fn pem(&self) -> &str {
        &self.pem
    }

    pub fn certificate(&self) -> CertificateDer<'static> {
        self.certificate.clone()
    }

    pub fn server_config(&self, host: &str) -> Result<Arc<ServerConfig>> {
        let key = KeyPair::generate().map_err(|_| Error::Certificate)?;
        let mut params =
            CertificateParams::new(vec![host.to_owned()]).map_err(|_| Error::Certificate)?;
        params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
        let leaf = params
            .signed_by(&key, &self.issuer)
            .map_err(|_| Error::Certificate)?;
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let mut config = ServerConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(|_| Error::Certificate)?
            .with_no_client_auth()
            .with_single_cert(
                vec![leaf.der().clone(), self.certificate.clone()],
                PrivatePkcs8KeyDer::from(key.serialize_der()).into(),
            )
            .map_err(|_| Error::Certificate)?;
        config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
        Ok(Arc::new(config))
    }
}
