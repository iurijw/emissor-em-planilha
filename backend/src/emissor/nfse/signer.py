"""Assinatura XMLDSig da DPS (e de pedidos de evento).

- Enveloped, RSA-SHA256, digest SHA-256, referência ao ``Id`` do elemento ``inf*``.
- Sem prefixo de namespace (``<Signature xmlns="...xmldsig#">``): a Sefin rejeita
  prefixos (E1228).
- Canonicalização exclusiva, igual à usada pela própria Sefin nos XMLs de retorno.
  Se a homologação rejeitar a assinatura, trocar ``C14N`` para
  ``CanonicalizationMethod.CANONICAL_XML_1_0`` e registrar em CLAUDE.md.
"""

from __future__ import annotations

from lxml import etree
from signxml import CanonicalizationMethod, DigestAlgorithm, SignatureMethod, XMLSigner, XMLVerifier

from emissor.nfse.certificate import Certificado
from emissor.nfse.constants import NS_DSIG

C14N = CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0


def assinar(root: etree._Element, cert: Certificado, id_elemento: str | None = None) -> etree._Element:
    """Assina ``root`` referenciando o primeiro filho com atributo ``Id``.

    Retorna um novo elemento (o original não é alterado).
    """
    if id_elemento is None:
        alvo = next((el for el in root if el.get("Id")), None)
        if alvo is None:
            raise ValueError("Nenhum elemento com atributo Id para assinar.")
        id_elemento = alvo.get("Id")
    signer = XMLSigner(
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm=C14N,
    )
    signer.namespaces = {None: NS_DSIG}
    assinado = signer.sign(
        root,
        key=cert.key,
        cert=[cert.cert_pem().decode("ascii")],
        reference_uri=f"#{id_elemento}",
        always_add_key_value=False,
    )
    # Com namespace padrão, o signxml cria <Signature> sem namespace na árvore em
    # memória (só a serialização sai correta). Reparse para a árvore ficar consistente
    # e a validação XSD enxergar o elemento no namespace xmldsig.
    return etree.fromstring(etree.tostring(assinado))


def verificar(root: etree._Element | bytes, cert_pem: bytes | None = None) -> bool:
    """Verifica a assinatura (uso em testes/diagnóstico)."""
    data = root if isinstance(root, bytes) else etree.tostring(root)
    XMLVerifier().verify(data, x509_cert=cert_pem.decode() if cert_pem else None, ignore_ambiguous_key_info=True)
    return True
