import requests
from bs4 import BeautifulSoup
import re
import urllib3
import logging

logger = logging.getLogger(__name__)

# Disable insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def scrape_website_summary(domain: str) -> str:
    """
    Scrape a website to extract its core business summary.
    Returns a brief description (title, meta desc, and first few paragraphs).
    Timeouts quickly to avoid blocking the engine.
    """
    if not domain or domain == "N/A":
        return "No domain provided."

    # Prepend schema if missing
    if not domain.startswith("http"):
        url = f"https://{domain}"
    else:
        url = domain
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    summary_parts = []
    
    try:
        # Try HTTPS first, very short timeout (5 seconds)
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        response.raise_for_status()
    except Exception as e:
        logger.warning(f"HTTPS scrape failed for {url}: {e}. Trying HTTP...")
        # Fallback to HTTP
        if url.startswith("https://"):
            url = url.replace("https://", "http://", 1)
            try:
                response = requests.get(url, headers=headers, timeout=10, verify=False)
                response.raise_for_status()
            except Exception as e2:
                logger.error(f"HTTP scrape also failed for {url}: {e2}")
                return f"Domain: {domain}"
        else:
            return f"Domain: {domain}"

    try:
        # Check content type
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return f"Domain: {domain}"

        soup = BeautifulSoup(response.text, "html.parser")

        # 1. Title
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        if title:
            summary_parts.append(f"Title: {title}")

        # 2. Meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            summary_parts.append(f"Description: {meta_desc['content'].strip()}")

        # 3. Main paragraphs (extract text up to ~500 chars)
        # Remove scripts, styles, nav, footers
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
            
        text_content = []
        for p in soup.find_all("p"):
            text = p.get_text(separator=" ", strip=True)
            if len(text) > 20: # skip very short meaningless paragraphs
                text_content.append(text)
                
        body_text = " ".join(text_content)
        # Clean up excessive whitespace
        body_text = re.sub(r'\s+', ' ', body_text).strip()
        
        if body_text:
            # take first 500 characters of meaningful text
            summary_parts.append(f"Website Content: {body_text[:500]}...")

        if not summary_parts:
            return f"Domain: {domain}"

        final_summary = " | ".join(summary_parts)
        return final_summary[:800] # Ensure it's not too long for the prompt

    except Exception as e:
        logger.error(f"Error parsing HTML for {domain}: {e}")
        return f"Domain: {domain}"

