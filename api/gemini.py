import os
import google.generativeai as genai
import json
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse  # Required for fixing links
from .config import load_config


from .clients.company_website_client import CompanyWebsiteClient

# Load .env file variables
load_dotenv()

config = load_config()
AI_MODEL = config["google"]["ai_model"]
# Configure the client
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


try:
    # Initialize the Gemini model
    model = genai.GenerativeModel(AI_MODEL)
except Exception as e:
    print(f"Viga mudeli initsialiseerimisel: {e}")
    exit()

company_website_client = CompanyWebsiteClient()


def get_website_text(url):
    """Downloads the content of a web page and cleans it into plain text."""
    print(f"   ... Downloading content from: {url}")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
        }
        # Use the injected CompanyWebsiteClient to fetch the page
        response = company_website_client.get_company_website(url, headers)

        # Parse the HTML content
        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script and style elements
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()

        # Extract text, clean up line breaks and multiple spaces
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        cleaned_text = "\n".join(chunk for chunk in chunks if chunk)

        print("   ... Sisu edukalt alla laetud ja puhastatud.")
        return cleaned_text

    except requests.exceptions.RequestException as e:
        print(f"   ... Viga veebilehe allalaadimisel: {e}")
        return None


def find_contact_page_url(base_url):
    """
    Step 1: Finds all links on the homepage and asks Gemini
    which one is most likely to contain contact information.
    """
    print(f"Step 1: Searching for the contact page on the homepage {base_url}...")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
        }
        # Use the injected CompanyWebsiteClient to fetch the page
        response = company_website_client.get_company_website(base_url, headers)

        soup = BeautifulSoup(response.text, "html.parser")

        links = []
        # Find all <a> (link) tags
        for a_tag in soup.find_all("a", href=True):
            link_text = a_tag.get_text(strip=True).lower()
            link_href = a_tag["href"]

            # Convert relative links (e.g., /contact) to full URLs
            full_url = urljoin(base_url, link_href)

            # Only include links that stay on the same domain
            if urlparse(full_url).netloc == urlparse(base_url).netloc:
                links.append(f"{link_text}: {full_url}")

        # Remove duplicates
        unique_links = list(set(links))

        if not unique_links:
            print("   ... Did not find any links on the homepage.")
            return base_url  # Return the original URL if no links are found

        # Construct the prompt for Gemini
        prompt = f"""
        The following is a list of links found on the website {base_url}.
        Which of these URLs most likely leads to a page containing
        company staff, team, or contact information (e.g., "Contact", "Team", "About Us")?

        List of links (in the format "link text: URL"):
        ---
        {" :: ".join(unique_links[:100])} 
        ---

        Please respond with only the single, most suitable URL. If url includes "team", "tiim", "meeskond", "staff", "team members", "tootajad", "töötajad" then this is most likely the best URL. Url containing "meist" is probably not the best URL. If none are suitable, return: "NONE"
        """

        print(prompt)

        # Ask Gemini
        response = model.generate_content(prompt)
        suggested_url = response.text.strip()

        if "NONE" in suggested_url or "http" not in suggested_url:
            print(
                f"   ... Gemini did not find a suitable subpage, using the main page: {base_url}"
            )
            return base_url
        else:
            print(f"   ... Gemini suggested the contact page: {suggested_url}")
            return suggested_url

    except requests.exceptions.RequestException as e:
        print(f"   ... Viga pealehe allalaadimisel: {e}")
        return base_url  # Use the original URL in case of errors


def run_full_staff_search(base_url):
    """
    Searches for staff information on a company website using Gemini AI.

    Args:
        base_url: The company website URL

    Returns:
        List of dictionaries containing staff information, each with:
        - name: Staff member's name
        - role: Their role/title
        - email: Email address (or None)
        - phone: Phone number (or None)

        Returns None if there was an error fetching the website content.
    """
    # 1. Step: Find the correct subpage (e.g., /team)
    contact_page_url = find_contact_page_url(base_url)

    # 2. Step: Download the content of that subpage
    print(f"\nStep 2: Downloading content from the identified page...")
    website_text = get_website_text(contact_page_url)

    if not website_text:
        print("Ei saanud veebilehe sisu kätte. Katkestan.")
        return None

    # 3. Step: Construct a new prompt and send it to Gemini
    prompt = f"""
    Your task is to act as a data analyst. Analyze the following website text and extract
    contact information for specific roles only.

    Return the data ONLY as a JSON array. Each object in the array must be in
    the following format:
    {{
      "name": "Name Here",
      "role": "Role Title Here",
      "email": "email address OR null",
      "phone": "phone number OR null"
    }}

    KEY ROLES TO FIND (by priority):
    I am interested ONLY in these roles. Please include Estonian equivalents.

    1. STRATEGIC LEADERSHIP:
       - 'CEO', 'Tegevjuht'
       - 'COO', 'Chief Operating Officer'
       - 'CIO', 'Chief Information Officer'
       - 'CTO', 'Chief Technology Officer'
       - 'Director'
       - 'Founder'

    2. DEVELOPMENT / TECHNOLOGY:
       - 'Arendusjuht', 'Head of Development'
       - 'IT-juht', 'IT Manager', 'Head of IT'
       - 'Innovatsioonijuht', 'Head of Innovation'

    3. PROJECT MANAGEMENT:
       - 'Projektijuht', 'Project Manager'

    4. PERSONNEL / MARKETING:
       - 'HR Manager', 'Personalijuht'
       - 'Head of Marketing', 'Turundusjuht'
       - 'Head of Sales', 'Müügijuht'

    5. GENERAL CONTACT:
       - 'General Contact'

    RULES:
    1. Find the name, role, email, AND phone number.
    2. If email or phone is not found, set the value to `null` (not "MISSING" or similar).
    3. Ignore all other roles that are not in the list above (e.g., "Project Manager", "Specialist" are too general).
    4. If you find a general contact (like "info@..." or a general phone number), add it as a separate object where the `role` is "General Contact".
    5. If you do not find ANY relevant contacts, return ONLY an empty array `[]`.
    6. Do not add anything else to your response (like "Here is the JSON:", "```json") besides the JSON itself.


    TEXT CONTENT:
    ---
    {website_text[:30000]} 
    ---
    Finish analysis and return ONLY JSON.
    """

    print("\nStep 3: Sending cleaned text to Gemini for analysis...")
    try:
        response = model.generate_content(prompt)

        print("\n--- Response (Raw Content) ---")
        # Clean up the response to show only JSON
        json_response = (
            response.text.strip().lstrip("```json").lstrip("```").rstrip("```")
        )

        # Fix reversed email addresses (a common anti-bot technique)
        print("\nStep 4: Fixing reversed email addresses...")
        try:
            data = json.loads(json_response)
            if isinstance(data, list):
                for item in data:
                    if (
                        isinstance(item, dict)
                        and "email" in item
                        and isinstance(item["email"], str)
                    ):
                        # Assuming the anti-bot technique reverses the email, e.g., "ee.tnak_tsim_oin" -> "nio_mist_kante.ee"
                        if item["email"].startswith("ee.") or item["email"].startswith(
                            "moc."
                        ):
                            # This specific check for "ee." seems tailored to a local/specific reversal pattern.
                            # A more general reversal check is:
                            # if '.' in item['email'] and '@' in item['email'] and item['email'] == item['email'][::-1][::-1]:
                            # For the specific anti-bot technique where the email is literally reversed:
                            # if item['email'] == item['email'][::-1]: # Check if it's a palindrome (unlikely)

                            # Sticking to the original logic which checks for a start pattern indicating a reversed string
                            # and performs a reversal:

                            item["email"] = item["email"][::-1]
            fixed_json = json.dumps(data, indent=2, ensure_ascii=False)
            print(fixed_json)
            print("--------------------------------\n")
            return data
        except json.JSONDecodeError as e:
            print(f"Viga JSON-i parsimisel: {e}")
            print(f"Response: {json_response}")
            return None

    except Exception as e:
        print(f"Viga päringu tegemisel: {e}")
        return None
