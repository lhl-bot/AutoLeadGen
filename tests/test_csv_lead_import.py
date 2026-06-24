import asyncio
import io
import json

from starlette.datastructures import UploadFile

import models
from routers.client_pools import import_pool_leads, preview_pool_lead_import
from services.csv_lead_import import parse_csv, suggest_mapping


def _seed_pool(db):
    user = models.User(username="owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    pool = models.ClientPool(user_id=user.id, name="Imported leads")
    db.add(pool)
    db.flush()
    return user, pool


def _upload(content: bytes, filename: str = "leads.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def test_csv_parser_supports_chinese_headers_and_gb18030():
    content = "公司名称,网站,邮箱,职位\n示例公司,example.com,buyer@example.com,采购经理\n".encode("gb18030")

    parsed = parse_csv(content)
    mapping = suggest_mapping(parsed.headers)

    assert parsed.encoding == "gb18030"
    assert parsed.rows[0]["公司名称"] == "示例公司"
    assert mapping["company_name"] == "公司名称"
    assert mapping["domain"] == "网站"
    assert mapping["email"] == "邮箱"


def test_csv_preview_marks_valid_duplicate_and_invalid_rows(db_session):
    user, pool = _seed_pool(db_session)
    db_session.add(models.Lead(
        client_pool_id=pool.id,
        domain="existing.com",
        email="old@existing.com",
        status="found",
    ))
    db_session.commit()
    content = (
        "公司名称,网站,邮箱,职位\n"
        "Existing,existing.com,new@existing.com,Buyer\n"
        "New,new.com,new@new.com,Founder\n"
        "Bad,not-a-domain,bad-email,Manager\n"
    ).encode("utf-8")

    result = asyncio.run(preview_pool_lead_import(
        pool.id,
        _upload(content),
        None,
        db_session,
        user,
    ))

    assert result["counts"] == {"valid": 1, "duplicate": 1, "invalid": 1}
    assert result["mapping"]["company_name"] == "公司名称"
    assert result["preview_rows"][0]["reason"] == "Duplicate domain"
    assert result["preview_rows"][1]["normalized"]["domain"] == "new.com"


def test_csv_import_uses_mapping_and_skips_duplicates(db_session):
    user, pool = _seed_pool(db_session)
    db_session.add(models.Lead(
        client_pool_id=pool.id,
        domain="existing.com",
        email="old@existing.com",
        status="found",
    ))
    db_session.commit()
    content = (
        "Business,Site,Work Mail,Role\n"
        "Existing,existing.com,new@existing.com,Buyer\n"
        "New,new.com,new@new.com,Founder\n"
        "No Email,no-email.com,,Manager\n"
    ).encode("utf-8")
    mapping = json.dumps({
        "company_name": "Business",
        "domain": "Site",
        "email": "Work Mail",
        "first_name": None,
        "last_name": None,
        "job_title": "Role",
        "linkedin_url": None,
        "whatsapp_number": None,
    })

    result = asyncio.run(import_pool_leads(
        pool.id,
        _upload(content),
        mapping,
        db_session,
        user,
    ))

    imported = (
        db_session.query(models.Lead)
        .filter(models.Lead.client_pool_id == pool.id, models.Lead.source_channel == "import")
        .order_by(models.Lead.domain)
        .all()
    )
    assert result["imported"] == 2
    assert result["duplicates"] == 1
    assert [lead.domain for lead in imported] == ["new.com", "no-email.com"]
    assert imported[0].status == "found"
    assert imported[1].status == "needs_email"
