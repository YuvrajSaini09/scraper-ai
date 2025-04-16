import re
import requests
from bs4 import BeautifulSoup
import streamlit as st
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor
import time
import json
import pandas as pd
import validators
import tldextract

# Set page title and layout
st.set_page_config(page_title="Structured Contact Scraper", layout="wide")

# Google Custom Search API key and configuration - hidden from frontend
GOOGLE_API_KEY = "AIzaSyDu0b_wic-Am7mXFxBU4-xUr8Cj3ifG_Ao"
GOOGLE_SEARCH_ENGINE_ID = "45687663363054394"

def is_valid_url(url):
    """Check if the URL is valid"""
    try:
        return validators.url(url)
    except:
        return False

def is_trash_email(email):
    """Check if email appears to be trash or disposable"""
    trash_domains = [
        'temp-mail', 'tempmail', 'disposable', 'mailinator', 'guerrilla', 'fake', 
        'yopmail', 'sharklasers', '10minutemail', 'trashmail', 'throwaway',
        'getnada', 'dispostable', 'mailnesia', 'spamgourmet', 'temp', 'tmpmail'
    ]
    
    # Check if domain contains trash keywords
    domain = email.split('@')[-1].lower()
    for trash in trash_domains:
        if trash in domain:
            return True
    
    # Check for random-looking local parts with excessive numbers/symbols
    local_part = email.split('@')[0].lower()
    if len(local_part) > 30:  # Excessively long local part
        return True
    
    # If local part is highly random (many digits mixed with letters)
    digit_count = sum(c.isdigit() for c in local_part)
    if digit_count > len(local_part) * 0.5 and len(local_part) > 10:
        return True
        
    return False

def extract_indian_phones(text):
    """Extract only valid Indian phone numbers (+91 and starting with 9,8,7,6)"""
    # Pattern for international format with Indian country code
    indian_cc_pattern = r'(?:\+91|0091)[- ]?(\d{5}[- ]?\d{5}|\d{3}[- ]?\d{3}[- ]?\d{4}|\d{10})'
    
    # Extract all potential Indian numbers with country code
    indian_cc_matches = re.findall(indian_cc_pattern, text)
    
    valid_phones = []
    for phone in indian_cc_matches:
        # Clean phone number of spaces and hyphens
        cleaned = re.sub(r'[- ]', '', phone)
        if len(cleaned) >= 10:  # Ensure we have at least 10 digits
            last_10 = cleaned[-10:]  # Get the last 10 digits
            # Check if the first digit is 9, 8, 7, or 6
            if last_10[0] in ['9', '8', '7', '6']:
                valid_phones.append(f"+91 {last_10}")
    
    # Pattern for 10-digit Indian numbers (starting with 9,8,7,6)
    indian_mobile_pattern = r'(?<!\d)(9|8|7|6)(\d{9})(?!\d)'
    
    # Extract 10-digit Indian mobile numbers
    indian_mobiles = re.findall(indian_mobile_pattern, text)
    for m in indian_mobiles:
        phone = m[0] + m[1]  # Combine first digit and remaining 9 digits
        if f"+91 {phone}" not in valid_phones:  # Avoid duplicates
            valid_phones.append(f"+91 {phone}")
    
    return valid_phones

def extract_emails(text):
    """Extract valid emails from text"""
    # Email regex pattern
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(email_pattern, text)
    
    # Filter out trash emails
    valid_emails = [email for email in emails if not is_trash_email(email)]
    
    return valid_emails

def extract_social_media(text, url):
    """Extract social media links from text and URL"""
    # Define patterns for social media URLs
    social_patterns = {
        'instagram': [r'instagram\.com/([A-Za-z0-9_\.]+)', r'instagram\.com/p/([A-Za-z0-9_\-]+)'],
        'facebook': [r'facebook\.com/([A-Za-z0-9\.]+)', r'fb\.com/([A-Za-z0-9\.]+)'],
        'whatsapp': [r'wa\.me/(\d+)', r'whatsapp\.com/(\d+)', r'api\.whatsapp\.com/send\?phone=(\d+)']
    }
    
    socials = {
        'instagram': "NULL",
        'facebook': "NULL",
        'whatsapp': "NULL"
    }
    
    # Extract from HTML
    for platform, patterns in social_patterns.items():
        for pattern in patterns:
            matches = re.findall(pattern, text)
            if matches:
                # For Instagram and Facebook, take the first match
                if platform in ['instagram', 'facebook']:
                    socials[platform] = f"https://{platform}.com/{matches[0]}"
                    break
                # For WhatsApp, format properly
                elif platform == 'whatsapp':
                    socials[platform] = f"https://wa.me/{matches[0]}"
                    break
    
    # Try to extract from the base URL (for domain.com/instagram redirect pages)
    base_domain = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(url))
    
    # Look for social media in the page's domain
    domain_parts = urlparse(url).netloc.split('.')
    if 'instagram' in domain_parts:
        socials['instagram'] = url
    elif 'facebook' in domain_parts or 'fb' in domain_parts:
        socials['facebook'] = url
    elif 'whatsapp' in domain_parts or 'wa' in domain_parts:
        socials['whatsapp'] = url
        
    return socials

def extract_business_info(soup, url):
    """Extract business name and other information"""
    business_info = {
        'name': "NULL",
        'business_name': "NULL",
        'website': url,
        'domain': "NULL",
        'location': "NULL"
    }
    
    # Extract domain/interest from meta tags and title
    try:
        # Get domain from URL
        extracted = tldextract.extract(url)
        domain = f"{extracted.domain}.{extracted.suffix}"
        business_info['domain'] = domain
        
        # Try to get business name from title
        title = soup.title.string if soup.title else ""
        if title:
            # Clean up title (remove common suffixes)
            title = re.sub(r'\s*[|\-–—]\s*.*$', '', title)
            title = re.sub(r'\s*[-–—:]\s*Home.*$', '', title)
            title = title.strip()
            if len(title) > 3:  # Ensure we have a meaningful title
                business_info['business_name'] = title
    except:
        pass
    
    # Try to find location - look for address patterns
    try:
        # Common location indicators
        location_indicators = ['address', 'location', 'headquarter', 'office', 'contact us']
        
        # Find elements that might contain address
        for indicator in location_indicators:
            elements = soup.find_all(string=re.compile(indicator, re.I))
            for element in elements:
                parent = element.parent
                # Look at the parent and its siblings for address-like content
                address_text = parent.get_text()
                
                # Check if text looks like an address (contains postal code or common address patterns)
                if re.search(r'\b\d{5,6}\b', address_text) or re.search(r'\b[A-Z][a-z]+,\s+[A-Z]{2}\b', address_text):
                    # Clean up the address text
                    address_text = re.sub(r'\s+', ' ', address_text).strip()
                    if len(address_text) > 10 and len(address_text) < 200:  # Reasonable address length
                        business_info['location'] = address_text
                        break
            
            if business_info['location'] != "NULL":
                break
    except:
        pass
    
    # Try to extract name from common patterns
    try:
        # Look for schema markup
        person_schema = soup.find('script', type='application/ld+json')
        if person_schema:
            try:
                data = json.loads(person_schema.string)
                if isinstance(data, dict):
                    # Check for Person or Organization
                    if data.get('@type') == 'Person' and data.get('name'):
                        business_info['name'] = data.get('name')
                    elif data.get('@type') == 'Organization' and data.get('name'):
                        business_info['business_name'] = data.get('name')
            except:
                pass
        
        # Look for common name patterns
        if business_info['name'] == "NULL":
            name_patterns = [
                r'[Aa]bout\s+([A-Z][a-z]+\s+[A-Z][a-z]+)',
                r'[Mm]y\s+name\s+is\s+([A-Z][a-z]+\s+[A-Z][a-z]+)',
                r'[Cc]ontact\s+([A-Z][a-z]+\s+[A-Z][a-z]+)'
            ]
            
            for pattern in name_patterns:
                matches = re.search(pattern, soup.get_text())
                if matches:
                    business_info['name'] = matches.group(1)
                    break
    except:
        pass
        
    return business_info

def get_links_from_page(url, session):
    """Extract all links from a webpage"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = session.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            base_url = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(url))
            
            links = []
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                # Make relative URLs absolute
                if href.startswith('/'):
                    href = urljoin(base_url, href)
                # Only include links from the same domain
                if href.startswith(base_url):
                    links.append(href)
            
            return list(set(links))
        return []
    except Exception as e:
        st.error(f"Error fetching links from {url}: {str(e)}")
        return []

def scrape_url(url, session, visited_urls):
    """Scrape a single URL for structured contact information"""
    if url in visited_urls:
        return None
    
    visited_urls.add(url)
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = session.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            text = response.text
            
            # Extract structured information
            emails = extract_emails(text)
            phones = extract_indian_phones(text)
            socials = extract_social_media(text, url)
            business_info = extract_business_info(soup, url)
            
            # Create contact entries
            contacts = []
            
            # If we have emails or phones
            if emails or phones:
                # If we have both emails and phones, match them
                if emails and phones:
                    for i in range(max(len(emails), len(phones))):
                        contact = {
                            'Name': business_info['name'],
                            'Mobile_no': phones[i] if i < len(phones) else "NULL",
                            'Email': emails[i] if i < len(emails) else "NULL",
                            'Location': business_info['location'],
                            'Instagram': socials['instagram'],
                            'Facebook': socials['facebook'],
                            'WhatsApp': socials['whatsapp'],
                            'Business_name': business_info['business_name'],
                            'Website_link': business_info['website'],
                            'Domain_interest': business_info['domain']
                        }
                        contacts.append(contact)
                # If we only have emails
                elif emails:
                    for email in emails:
                        contact = {
                            'Name': business_info['name'],
                            'Mobile_no': "NULL",
                            'Email': email,
                            'Location': business_info['location'],
                            'Instagram': socials['instagram'],
                            'Facebook': socials['facebook'],
                            'WhatsApp': socials['whatsapp'],
                            'Business_name': business_info['business_name'],
                            'Website_link': business_info['website'],
                            'Domain_interest': business_info['domain']
                        }
                        contacts.append(contact)
                # If we only have phones
                elif phones:
                    for phone in phones:
                        contact = {
                            'Name': business_info['name'],
                            'Mobile_no': phone,
                            'Email': "NULL",
                            'Location': business_info['location'],
                            'Instagram': socials['instagram'],
                            'Facebook': socials['facebook'],
                            'WhatsApp': socials['whatsapp'],
                            'Business_name': business_info['business_name'],
                            'Website_link': business_info['website'],
                            'Domain_interest': business_info['domain']
                        }
                        contacts.append(contact)
            # If we have no emails or phones but have other info
            elif any(socials.values()) or business_info['business_name'] != "NULL":
                contact = {
                    'Name': business_info['name'],
                    'Mobile_no': "NULL",
                    'Email': "NULL",
                    'Location': business_info['location'],
                    'Instagram': socials['instagram'],
                    'Facebook': socials['facebook'],
                    'WhatsApp': socials['whatsapp'],
                    'Business_name': business_info['business_name'],
                    'Website_link': business_info['website'],
                    'Domain_interest': business_info['domain']
                }
                contacts.append(contact)
            
            return contacts
        return None
    except Exception as e:
        st.error(f"Error scraping {url}: {str(e)}")
        return None

def get_google_maps_urls(keyword, location, num_results=5):
    """Get URLs from Google Maps search"""
    search_query = f"{keyword} in {location} google maps"
    urls = search_by_keyword(search_query, num_results)
    # Filter for maps URLs
    maps_urls = [url for url in urls if "google.com/maps" in url]
    return maps_urls

def get_targeted_urls(keyword):
    """Get URLs targeted for specific industries based on keyword"""
    keyword_lower = keyword.lower()
    targeted_urls = []
    
    # Local Services, Small Businesses
    if any(term in keyword_lower for term in ["salon", "shop", "store", "local", "business", "restaurant", "cafe", "bakery", "spa", "gym"]):
        targeted_urls.extend([
            "https://www.justdial.com/",
            "https://www.sulekha.com/",
            "https://www.nearmetrade.com/",
            "https://www.clickindia.com/",
            "https://www.yellowpages.in/"
        ])
    
    # Schools, Colleges, Institutes
    elif any(term in keyword_lower for term in ["school", "college", "university", "institute", "academy", "education", "coaching"]):
        targeted_urls.extend([
            "https://www.icbse.com/",
            "https://www.schoolmykids.com/",
            "https://www.edustoke.com/",
            "https://www.indiastudychannel.com/"
        ])
    
    # Micro Influencers, Creators
    elif any(term in keyword_lower for term in ["influencer", "creator", "youtuber", "blogger", "vlogger", "content"]):
        targeted_urls.extend([
            "https://starngage.com/",
            "https://influence.co/"
        ])
    
    # Ad Agencies, Marketing Services
    elif any(term in keyword_lower for term in ["agency", "marketing", "advertising", "digital", "media", "seo", "ppc"]):
        targeted_urls.extend([
            "https://www.clutch.co/",
            "https://www.sortlist.com/",
            "https://www.goodfirms.co/",
            "https://upcity.com/",
            "https://www.designrush.com/"
        ])
    
    # Editing Agencies, Creative Studios
    elif any(term in keyword_lower for term in ["edit", "studio", "production", "creative", "design", "video", "film", "photography"]):
        targeted_urls.extend([
            "https://www.behance.net/",
            "https://dribbble.com/",
            "https://www.productionhub.com/",
            "https://www.fiverr.com/"
        ])
    
    # Business Owners, Freelancers, Startups
    elif any(term in keyword_lower for term in ["startup", "founder", "entrepreneur", "freelancer", "ceo", "owner", "business"]):
        targeted_urls.extend([
            "https://www.startupindia.gov.in/",
            "https://angel.co/",
            "https://www.crunchbase.com/",
            "https://yourstory.com/"
        ])
    
    return targeted_urls

def search_by_keyword(keyword, num_results=10):
    """Return a list of URLs from Google search based on keyword"""
    urls = []
    
    # Create a Google dork query based on the keyword
    # Check if it looks like a business type + location query
    if " in " in keyword.lower():
        parts = keyword.lower().split(" in ")
        business_type = parts[0].strip()
        location = parts[1].strip()
        
        # Use the dork template
        dork_query = f'"{business_type}" "{location}" ("contact" OR "email" OR "phone") site:.in'
    else:
        # Just use the keyword
        dork_query = f'{keyword} ("contact" OR "email" OR "phone")'
    
    try:
        # Calculate how many API calls we need (Google CSE returns max 10 results per call)
        num_calls = (num_results + 9) // 10
        
        for i in range(num_calls):
            start_index = i * 10 + 1
            search_url = f"https://www.googleapis.com/customsearch/v1"
            params = {
                'key': GOOGLE_API_KEY,
                'cx': GOOGLE_SEARCH_ENGINE_ID,
                'q': dork_query,
                'start': start_index,
                'num': min(10, num_results - len(urls))
            }
            
            response = requests.get(search_url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                
                if 'items' in data:
                    for item in data['items']:
                        urls.append(item['link'])
                        
                        # Stop if we've reached the requested number of results
                        if len(urls) >= num_results:
                            break
                else:
                    st.warning("No search results found.")
                    break
            else:
                st.error(f"Search API error: {response.status_code}")
                st.error(response.text)
                break
                
            # Respect API limits
            time.sleep(1)
            
    except Exception as e:
        st.error(f"Error during search: {str(e)}")
    
    return urls

def main():
    st.title("📊 Structured Contact Data Scraper")
    
    st.markdown("""
    This tool scrapes websites for contact information and organizes it in a structured format.
    Enter URLs directly or provide search keywords to find relevant sites.
    """)
    
    tab1, tab2 = st.tabs(["URL Scraper", "Keyword Search"])
    
    with tab1:
        st.subheader("Scrape by URLs")
        urls_input = st.text_area("Enter URLs (one per line):", height=150,
                                  placeholder="https://example.com\nhttps://anothersite.com")
        
        max_depth = st.slider("Crawl Depth (higher values take longer)", 0, 3, 1,
                             help="0 = only the entered URLs, 1 = also follow links on those pages, etc.")
        
        col1, col2 = st.columns(2)
        with col1:
            search_button = st.button("Start Scraping", type="primary", use_container_width=True)
        with col2:
            clear_button = st.button("Clear Results", type="secondary", use_container_width=True)
            
    with tab2:
        st.subheader("Scrape by Keywords")
        keyword = st.text_input("Enter search keyword:", placeholder="salon in Mumbai")
        
        # Add location field to enhance search
        location = st.text_input("Location (optional):", placeholder="Mumbai")
        
        # Add industry/category selection
        industry_options = [
            "Local Services/Small Businesses",
            "Schools/Educational Institutes",
            "Micro Influencers/Creators",
            "Ad Agencies/Marketing Services",
            "Editing Agencies/Creative Studios",
            "Business Owners/Startups",
            "Any Category"
        ]
        selected_industry = st.selectbox("Select Category:", industry_options)
        
        num_results = st.slider("Number of search results to scrape:", 5, 50, 10)
        max_depth_keyword = st.slider("Crawl Depth for Search Results:", 0, 2, 0,
                                     help="How deep to crawl on each search result")
        
        include_maps = st.checkbox("Include Google Maps results", value=True)
        
        col1, col2 = st.columns(2)
        with col1:
            keyword_search_button = st.button("Search & Scrape", type="primary", use_container_width=True)
        with col2:
            keyword_clear_button = st.button("Clear Keyword Results", type="secondary", use_container_width=True)
    
    # Initialize session state if not exists
    if 'contacts' not in st.session_state:
        st.session_state.contacts = []
        st.session_state.scanned_urls = set()
    
    # Clear results if requested
    if clear_button or keyword_clear_button:
        st.session_state.contacts = []
        st.session_state.scanned_urls = set()
        st.experimental_rerun()
    
    # Main scraping logic for direct URLs
    if search_button and urls_input.strip():
        urls = [url.strip() for url in urls_input.splitlines() if url.strip()]
        valid_urls = [url for url in urls if is_valid_url(url)]
        
        if not valid_urls:
            st.error("Please enter at least one valid URL")
        else:
            with st.spinner(f"Scraping {len(valid_urls)} URLs with depth {max_depth}..."):
                # Create a session for reusing connections
                session = requests.Session()
                
                # Start with the input URLs
                urls_to_process = valid_urls
                visited_urls = set()
                
                # Process URLs up to the specified depth
                for depth in range(max_depth + 1):
                    if not urls_to_process:
                        break
                    
                    st.info(f"Processing depth {depth}: {len(urls_to_process)} URLs")
                    progress_bar = st.progress(0)
                    
                    next_level_urls = []
                    
                    # Use ThreadPoolExecutor for parallel processing
                    with ThreadPoolExecutor(max_workers=5) as executor:
                        future_to_url = {
                            executor.submit(scrape_url, url, session, visited_urls): url 
                            for url in urls_to_process if url not in visited_urls
                        }
                        
                        for i, future in enumerate(future_to_url):
                            url = future_to_url[future]
                            try:
                                contacts = future.result()
                                if contacts:
                                    st.session_state.contacts.extend(contacts)
                                st.session_state.scanned_urls.add(url)
                                
                                # If not at max depth, collect links for next level
                                if depth < max_depth:
                                    new_links = get_links_from_page(url, session)
                                    next_level_urls.extend(new_links)
                                
                            except Exception as e:
                                st.error(f"Error processing {url}: {str(e)}")
                            
                            # Update progress
                            progress_bar.progress((i + 1) / len(future_to_url))
                    
                    # Set up the next level of URLs for processing
                    urls_to_process = list(set(next_level_urls))
                    
                    # Small delay to prevent overwhelming the target servers
                    time.sleep(1)
    
    # Keyword search logic
    if keyword_search_button and keyword.strip():
        with st.spinner(f"Searching for '{keyword}' and scraping results..."):
            # Combine keyword and location if both provided
            if location and location.strip():
                combined_keyword = f"{keyword} in {location}"
            else:
                combined_keyword = keyword
            
            # Get targeted URLs based on selected industry/category
            st.info("Identifying targeted websites for your search...")
            targeted_sites = []
            
            if selected_industry != "Any Category":
                if "Local Services" in selected_industry:
                    targeted_sites = [
                        "justdial.com", "sulekha.com", "nearmetrade.com", 
                        "clickindia.com", "yellowpages.in"
                    ]
                elif "Schools" in selected_industry:
                    targeted_sites = [
                        "icbse.com", "schoolmykids.com", "edustoke.com", 
                        "indiastudychannel.com"
                    ]
                elif "Micro Influencers" in selected_industry:
                    targeted_sites = [
                        "instagram.com", "starngage.com", "influence.co"
                    ]
                elif "Ad Agencies" in selected_industry:
                    targeted_sites = [
                        "clutch.co", "sortlist.com", "goodfirms.co", 
                        "upcity.com", "designrush.com"
                    ]
                elif "Editing Agencies" in selected_industry:
                    targeted_sites = [
                        "behance.net", "dribbble.com", "productionhub.com", 
                        "fiverr.com"
                    ]
                elif "Business Owners" in selected_industry:
                    targeted_sites = [
                        "linkedin.com", "startupindia.gov.in", "angel.co", 
                        "crunchbase.com", "yourstory.com"
                    ]
            
            # Get URLs from search results
            st.info("Fetching targeted search results...")
            search_urls = []
            
            # 1. First, try targeted sites with specific dorks
            if targeted_sites:
                for site in targeted_sites[:3]:  # Limit to first 3 to avoid too many API calls
                    site_dork = f'{combined_keyword} ("contact" OR "email" OR "phone") site:{site}'
                    site_urls = search_by_keyword(site_dork, 5)  # Get 5 results per site
                    search_urls.extend(site_urls)
            
            # 2. Get general search results
            general_results_needed = max(5, num_results - len(search_urls))
            general_urls = search_by_keyword(combined_keyword, general_results_needed)
            search_urls.extend(general_urls)
            
            # 3. Add Google Maps results if requested
            if include_maps and location.strip():
                st.info("Fetching Google Maps results...")
                maps_urls = get_google_maps_urls(keyword, location, 5)
                search_urls.extend(maps_urls)
            
            # Remove duplicates and limit to requested number
            search_urls = list(dict.fromkeys(search_urls))[:num_results]
            
            if search_urls:
                st.success(f"Found {len(search_urls)} URLs. Starting to scrape...")
                
                # Create a session for reusing connections
                session = requests.Session()
                visited_urls = set()
                
                # Process search results
                progress_bar = st.progress(0)
                
                # First process the search result URLs directly
                urls_to_process = search_urls
                
                # Process URLs up to the specified depth
                for depth in range(max_depth_keyword + 1):
                    if not urls_to_process:
                        break
                    
                    st.info(f"Processing depth {depth}: {len(urls_to_process)} URLs")
                    depth_progress = st.progress(0)
                    
                    next_level_urls = []
                    
                    # Use ThreadPoolExecutor for parallel processing
                    with ThreadPoolExecutor(max_workers=5) as executor:
                        future_to_url = {
                            executor.submit(scrape_url, url, session, visited_urls): url 
                            for url in urls_to_process if url not in visited_urls
                        }
                        
                        for i, future in enumerate(future_to_url):
                            url = future_to_url[future]
                            try:
                                contacts = future.result()
                                if contacts:
                                    st.session_state.contacts.extend(contacts)
                                st.session_state.scanned_urls.add(url)
                                
                                
# If not at max depth, collect links for next level
                                if depth < max_depth_keyword:
                                    new_links = get_links_from_page(url, session)
                                    next_level_urls.extend(new_links)
                                
                            except Exception as e:
                                st.error(f"Error processing {url}: {str(e)}")
                            
                            # Update progress
                            depth_progress.progress((i + 1) / len(future_to_url))
                    
                    # Set up the next level of URLs for processing
                    urls_to_process = list(set(next_level_urls))
                    
                    # Small delay to prevent overwhelming the target servers
                    time.sleep(1)
                    
                progress_bar.progress(1.0)
            else:
                st.warning("No search results found or search API configuration is incomplete.")
    
    # Display results if any
    if st.session_state.contacts:
        st.divider()
        st.subheader("📊 Scraping Results")
        
        # Create DataFrame for display
        df = pd.DataFrame(st.session_state.contacts)
        
        # Remove duplicates
        if not df.empty:
            df = df.drop_duplicates(subset=['Email', 'Mobile_no'], keep='first')
            
            # Filter out columns where all values are "NULL"
            cols_to_drop = []
            for col in df.columns:
                if (df[col] == "NULL").all():
                    cols_to_drop.append(col)
            
            # Drop the all-NULL columns
            if cols_to_drop:
                df = df.drop(columns=cols_to_drop)
        
        # Display metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Contacts Found", len(df))
        with col2:
            email_count = len(df[df['Email'] != "NULL"]) if 'Email' in df.columns else 0
            st.metric("Emails Found", email_count)
        with col3:
            phone_count = len(df[df['Mobile_no'] != "NULL"]) if 'Mobile_no' in df.columns else 0
            st.metric("Phone Numbers Found", phone_count)
        
        # Display data table
        st.dataframe(df, use_container_width=True)
        
        # Download options
        col1, col2 = st.columns(2)
        with col1:
            # CSV Download
            csv = df.to_csv(index=False)
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name="structured_contacts.csv",
                mime="text/csv",
                use_container_width=True
            )
        with col2:
            # Excel Download
            buffer = pd.ExcelWriter('structured_contacts.xlsx', engine='xlsxwriter')
            df.to_excel(buffer, index=False, sheet_name='Contacts')
            buffer.save()
            
            with open('structured_contacts.xlsx', 'rb') as f:
                excel_data = f.read()
            
            st.download_button(
                label="Download Excel",
                data=excel_data,
                file_name="structured_contacts.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

if __name__ == "__main__":
    main()
