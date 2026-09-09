from __future__ import annotations

import html
import re
from urllib.parse import unquote, urljoin, urlparse

from domain.errors import ErrorCategory, ScrapeFailure
from domain.identity import detail_record_identity
from fetching.urls import origin
from domain.models import DocumentBundle, PayloadDocument, Site


def expected_record_id(site: Site) -> str:
    if site.payload == "admin_article_api":
        match = re.search(r"/article/(?:admin|manager|lottery)/([^/?#]+)", urlparse(site.url).path, flags=re.I)
        return match.group(1) if match else ""
    if site.payload == "tuku_user_forums":
        return detail_record_identity(site.url)
    if site.payload == "topic_list_detail":
        return ""
    return detail_record_identity(site.url)


def validate_bundle_boundaries(bundle: DocumentBundle, site: Site) -> None:
    if not bundle.scan_complete:
        raise ScrapeFailure(ErrorCategory.STRUCTURE_CHANGED, "文档扫描未完成")
    expected = expected_record_id(site)
    if not expected:
        return
    missing = [document.label for document in bundle.documents if not document.record_id]
    if missing:
        raise ValueError(f"记录边界缺失：目标为{expected}，文档未绑定：{','.join(missing)}")
    mismatched = sorted({document.record_id for document in bundle.documents if document.record_id != expected})
    if mismatched:
        label = "文章ID" if site.payload == "admin_article_api" else "用户ID"
        raise ValueError(f"{label}边界冲突：目标边界为{expected}，文档含{','.join(mismatched)}")


def validate_document_relationships(bundle: DocumentBundle) -> None:
    documents_by_url: dict[str, list[PayloadDocument]] = {}
    for document in bundle.documents:
        if document.url:
            documents_by_url.setdefault(document.url, []).append(document)
    for document in bundle.documents:
        actual = detail_record_identity(document.url)
        if document.own_record_id and actual and document.own_record_id != actual:
            raise ValueError(f"文档自身记录边界冲突：{document.label}自身ID与URL不一致")
        actual = actual or document.own_record_id
        if actual and document.record_id and actual != document.record_id:
            # Admin APIs carry a bare article ID in the existing data contract.
            if actual != "admin_article:" + document.record_id:
                raise ValueError(f"文档自身记录边界冲突：{document.label} {actual}")
        if document.identity_inherited and (not document.parent_url or not document.link_reference):
            raise ValueError(f"继承身份缺少父文档引用证据：{document.label}")
        if not document.parent_url:
            continue
        if not document.link_reference:
            raise ValueError(f"文档关系边界缺失：{document.label}缺少链接引用")
        parents = [
            parent
            for parent in documents_by_url.get(document.parent_url, ())
            if parent is not document
        ]
        if not parents:
            raise ValueError(f"文档关系边界缺失：{document.label}父文档{document.parent_url}不在DocumentBundle内")
        parent_ids = {parent.record_id for parent in parents if parent.record_id}
        if document.record_id and parent_ids and document.record_id not in parent_ids:
            raise ValueError(
                f"文档关系记录ID冲突：{document.label}={document.record_id}，"
                f"父文档={','.join(sorted(parent_ids))}"
            )
        if document.parent_url != document.url and not any(
            document_is_linked(parent, document) for parent in parents
        ):
            raise ValueError(f"文档关系边界无效：父文档未引用{document.url}")


def document_is_linked(anchor: PayloadDocument, body: PayloadDocument) -> bool:
    if anchor is body or not body.url or (anchor.url and anchor.url == body.url):
        return False
    anchor_source = html.unescape(anchor.source.replace(r"\/", "/"))
    parsed = urlparse(body.url)
    link_reference = html.unescape(body.link_reference.replace(r"\/", "/")).strip()
    if (
        link_reference
        and link_reference in anchor_source
        and urljoin(anchor.url, link_reference) == body.url
    ):
        return True
    relative = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    references = [body.url, unquote(body.url)]
    protocol_relative = f"//{parsed.netloc}{relative}" if parsed.netloc else ""
    references.extend((protocol_relative, unquote(protocol_relative)))
    if any(reference and reference != "/" and reference in anchor_source for reference in references):
        return True
    anchor_url = urlparse(anchor.url)
    if (anchor_url.scheme.lower(), anchor_url.netloc.lower()) != (parsed.scheme.lower(), parsed.netloc.lower()):
        return False
    relative_references = [relative, unquote(relative)]
    if not parsed.query:
        relative_references.append(parsed.path)
    return any(reference and reference != "/" and reference in anchor_source for reference in relative_references)


def linked_document_is_authorized(site: Site, anchor: PayloadDocument, body: PayloadDocument) -> bool:
    if not body.link_reference or not body.parent_url:
        return False
    configured = bool(
        site.linked_document_pattern
        and re.match(site.linked_document_pattern, body.url, flags=re.I)
    )
    direct_link = body.parent_url == anchor.url and document_is_linked(anchor, body)
    configured_siblings = bool(
        configured
        and anchor.parent_url
        and anchor.link_reference
        and anchor.parent_url == body.parent_url
        and re.match(site.linked_document_pattern, anchor.url, flags=re.I)
    )
    if configured_siblings:
        return True
    if not direct_link:
        return False
    anchor_url = urlparse(anchor.url)
    body_url = urlparse(body.url)
    same_origin = (
        anchor_url.scheme.lower(),
        anchor_url.netloc.lower(),
    ) == (
        body_url.scheme.lower(),
        body_url.netloc.lower(),
    )
    allowed_origin = origin(body.url) in {origin(value) for value in site.allowed_document_origins}
    body_path = body_url.path.lower()
    directly_referenced_data_script = (
        site.payload
        in {"page_and_scripts", "curl_tls10_page_and_scripts", "scripts", "topic_list_detail"}
        and body_path.startswith("/upload/script/")
        and body_path.endswith(".js")
    )
    # direct_link already proves that this exact URL came from the parent
    # document.  Keep arbitrary cross-origin documents blocked, while allowing
    # the site's established external data-script layout.
    return same_origin or configured or allowed_origin or directly_referenced_data_script
