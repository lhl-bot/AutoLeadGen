"""
Timezone resolver — maps TLDs and country names to IANA timezone strings.
Used to auto-set Lead.timezone for timezone-aware email delivery.
"""
from typing import Optional

# Country code TLD → primary IANA timezone
_TLD_TIMEZONE_MAP = {
    "de": "Europe/Berlin",
    "fr": "Europe/Paris",
    "it": "Europe/Rome",
    "es": "Europe/Madrid",
    "pt": "Europe/Lisbon",
    "nl": "Europe/Amsterdam",
    "be": "Europe/Brussels",
    "at": "Europe/Vienna",
    "ch": "Europe/Zurich",
    "se": "Europe/Stockholm",
    "no": "Europe/Oslo",
    "dk": "Europe/Copenhagen",
    "fi": "Europe/Helsinki",
    "pl": "Europe/Warsaw",
    "cz": "Europe/Prague",
    "hu": "Europe/Budapest",
    "ro": "Europe/Bucharest",
    "gr": "Europe/Athens",
    "ie": "Europe/Dublin",
    "uk": "Europe/London",
    "co.uk": "Europe/London",
    "jp": "Asia/Tokyo",
    "kr": "Asia/Seoul",
    "cn": "Asia/Shanghai",
    "sg": "Asia/Singapore",
    "in": "Asia/Kolkata",
    "au": "Australia/Sydney",
    "nz": "Pacific/Auckland",
    "br": "America/Sao_Paulo",
    "mx": "America/Mexico_City",
    "ar": "America/Argentina/Buenos_Aires",
    "cl": "America/Santiago",
    "co": "America/Bogota",
    "ca": "America/Toronto",
    "us": "America/New_York",
    "ae": "Asia/Dubai",
    "sa": "Asia/Riyadh",
    "tr": "Europe/Istanbul",
    "za": "Africa/Johannesburg",
    "eg": "Africa/Cairo",
    "ng": "Africa/Lagos",
    "ke": "Africa/Nairobi",
}

# Country name → IANA timezone (case-insensitive lookup)
_COUNTRY_TIMEZONE_MAP = {
    "germany": "Europe/Berlin",
    "france": "Europe/Paris",
    "italy": "Europe/Rome",
    "spain": "Europe/Madrid",
    "portugal": "Europe/Lisbon",
    "netherlands": "Europe/Amsterdam",
    "belgium": "Europe/Brussels",
    "austria": "Europe/Vienna",
    "switzerland": "Europe/Zurich",
    "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo",
    "denmark": "Europe/Copenhagen",
    "finland": "Europe/Helsinki",
    "poland": "Europe/Warsaw",
    "czech republic": "Europe/Prague",
    "hungary": "Europe/Budapest",
    "romania": "Europe/Bucharest",
    "greece": "Europe/Athens",
    "ireland": "Europe/Dublin",
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "japan": "Asia/Tokyo",
    "south korea": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "china": "Asia/Shanghai",
    "singapore": "Asia/Singapore",
    "india": "Asia/Kolkata",
    "australia": "Australia/Sydney",
    "new zealand": "Pacific/Auckland",
    "brazil": "America/Sao_Paulo",
    "mexico": "America/Mexico_City",
    "argentina": "America/Argentina/Buenos_Aires",
    "chile": "America/Santiago",
    "colombia": "America/Bogota",
    "canada": "America/Toronto",
    "united states": "America/New_York",
    "usa": "America/New_York",
    "uae": "Asia/Dubai",
    "united arab emirates": "Asia/Dubai",
    "saudi arabia": "Asia/Riyadh",
    "turkey": "Europe/Istanbul",
    "south africa": "Africa/Johannesburg",
    "egypt": "Africa/Cairo",
    "nigeria": "Africa/Lagos",
    "kenya": "Africa/Nairobi",
}


def guess_timezone_from_domain(domain: str) -> Optional[str]:
    """Infer timezone from domain TLD (e.g., .de → Europe/Berlin)."""
    if not domain:
        return None
    parts = domain.lower().strip().split(".")
    
    # Try compound TLD first (e.g., .co.uk)
    if len(parts) >= 3:
        compound_tld = ".".join(parts[-2:])
        if compound_tld in _TLD_TIMEZONE_MAP:
            return _TLD_TIMEZONE_MAP[compound_tld]
    
    # Single TLD
    if len(parts) >= 2:
        tld = parts[-1]
        if tld in _TLD_TIMEZONE_MAP:
            return _TLD_TIMEZONE_MAP[tld]
    
    # .com, .net, .org — can't determine; return None
    return None


def guess_timezone_from_country(country: str) -> Optional[str]:
    """Map a country name to its primary IANA timezone."""
    if not country:
        return None
    return _COUNTRY_TIMEZONE_MAP.get(country.lower().strip())
