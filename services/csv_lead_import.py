import csv
import io
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse


MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 5000
IMPORT_FIELDS = (
    "company_name",
    "domain",
    "email",
    "first_name",
    "last_name",
    "job_title",
    "linkedin_url",
    "whatsapp_number",
)

HEADER_ALIASES = {
    "company_name": {
        "company", "companyname", "business", "organization", "organisation",
        "公司", "公司名称", "企业", "企业名称",
    },
    "domain": {
        "domain", "website", "site", "url", "companywebsite",
        "域名", "网站", "网址", "公司网站",
    },
    "email": {
        "email", "emailaddress", "mail", "workemail",
        "邮箱", "电子邮箱", "邮件", "工作邮箱",
    },
    "first_name": {
        "firstname", "givenname", "contactfirstname",
        "名", "名字", "联系人名",
    },
    "last_name": {
        "lastname", "surname", "familyname", "contactlastname",
        "姓", "姓氏", "联系人姓氏",
    },
    "job_title": {
        "jobtitle", "title", "position", "role",
        "职位", "职称", "岗位", "角色",
    },
    "linkedin_url": {
        "linkedin", "linkedinurl", "linkedinprofile",
        "领英", "领英链接", "linkedin链接",
    },
    "whatsapp_number": {
        "whatsapp", "whatsappnumber", "phone", "phonenumber", "mobile",
        "电话", "手机号", "手机", "whatsapp号码",
    },
}


@dataclass
class ParsedCsv:
    encoding: str
    delimiter: str
    headers: List[str]
    rows: List[Dict[str, str]]


def _header_key(value: str) -> str:
    return re.sub(r"[\s_\-./]+", "", (value or "").strip().lower())


def decode_csv(content: bytes) -> Tuple[str, str]:
    if not content:
        raise ValueError("CSV file is empty")
    if len(content) > MAX_CSV_BYTES:
        raise ValueError("CSV file exceeds the 2 MB limit")

    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("Unsupported CSV encoding; use UTF-8 or GB18030")


def parse_csv(content: bytes) -> ParsedCsv:
    text, encoding = decode_csv(content)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [str(header or "").strip() for header in (reader.fieldnames or [])]
    if not headers or not any(headers):
        raise ValueError("CSV header row is missing")
    if len(set(headers)) != len(headers):
        raise ValueError("CSV contains duplicate column names")

    rows = []
    for row_number, row in enumerate(reader, start=2):
        if row_number > MAX_CSV_ROWS + 1:
            raise ValueError(f"CSV exceeds the {MAX_CSV_ROWS} row limit")
        cleaned = {
            str(key or "").strip(): str(value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        if any(cleaned.values()):
            rows.append(cleaned)
    if not rows:
        raise ValueError("CSV contains no data rows")
    return ParsedCsv(
        encoding=encoding,
        delimiter=delimiter,
        headers=headers,
        rows=rows,
    )


def suggest_mapping(headers: Iterable[str]) -> Dict[str, Optional[str]]:
    normalized_headers = {_header_key(header): header for header in headers}
    mapping: Dict[str, Optional[str]] = {}
    for field in IMPORT_FIELDS:
        source = None
        for alias in HEADER_ALIASES[field]:
            source = normalized_headers.get(_header_key(alias))
            if source:
                break
        mapping[field] = source
    return mapping


def validate_mapping(
    mapping: Dict[str, Optional[str]],
    headers: Iterable[str],
    *,
    require_identity: bool = True,
) -> Dict[str, Optional[str]]:
    header_set = set(headers)
    cleaned: Dict[str, Optional[str]] = {}
    for field in IMPORT_FIELDS:
        source = mapping.get(field)
        if source in (None, ""):
            cleaned[field] = None
        elif source not in header_set:
            raise ValueError(f"Mapped column '{source}' does not exist")
        else:
            cleaned[field] = source
    if require_identity and not cleaned["domain"] and not cleaned["email"]:
        raise ValueError("Map at least a domain/website or email column")
    return cleaned


def normalize_email(value: str) -> Optional[str]:
    email = (value or "").strip().lower()
    if not email:
        return None
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return None
    return email


def normalize_domain(value: str) -> Optional[str]:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    domain = (parsed.hostname or "").strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or "." not in domain:
        return None
    return domain


def normalize_import_row(
    row: Dict[str, str],
    mapping: Dict[str, Optional[str]],
) -> Dict[str, Optional[str]]:
    def value(field: str) -> str:
        source = mapping.get(field)
        return (row.get(source, "") if source else "").strip()

    email = normalize_email(value("email"))
    domain = normalize_domain(value("domain"))
    if not domain and email:
        domain = normalize_domain(email.split("@", 1)[1])

    return {
        "company_name": value("company_name") or None,
        "domain": domain,
        "email": email,
        "first_name": value("first_name") or None,
        "last_name": value("last_name") or None,
        "job_title": value("job_title") or None,
        "linkedin_url": value("linkedin_url") or None,
        "whatsapp_number": value("whatsapp_number") or None,
    }


def analyze_import_rows(
    rows: List[Dict[str, str]],
    mapping: Dict[str, Optional[str]],
    *,
    existing_emails: Set[str],
    existing_domains: Set[str],
) -> List[Dict]:
    seen_emails: Set[str] = set()
    seen_domains: Set[str] = set()
    results = []

    for index, row in enumerate(rows, start=2):
        normalized = normalize_import_row(row, mapping)
        email = normalized["email"]
        domain = normalized["domain"]
        status = "valid"
        reason = None

        mapped_email_source = mapping.get("email")
        raw_email = row.get(mapped_email_source, "").strip() if mapped_email_source else ""
        mapped_domain_source = mapping.get("domain")
        raw_domain = row.get(mapped_domain_source, "").strip() if mapped_domain_source else ""

        if raw_email and not email:
            status, reason = "invalid", "Invalid email format"
        elif raw_domain and not domain and not email:
            status, reason = "invalid", "Invalid domain or website"
        elif not domain:
            status, reason = "invalid", "A valid domain or email is required"
        elif email and (email in existing_emails or email in seen_emails):
            status, reason = "duplicate", "Duplicate email"
        elif domain in existing_domains or domain in seen_domains:
            status, reason = "duplicate", "Duplicate domain"

        if status == "valid":
            if email:
                seen_emails.add(email)
            seen_domains.add(domain)

        results.append({
            "row_number": index,
            "status": status,
            "reason": reason,
            "normalized": normalized,
            "raw": row,
        })
    return results
