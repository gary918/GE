import logging
import sys
import os
import google.auth
from google.auth.transport.requests import AuthorizedSession

# --- Load .env file ---
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

# --- Configuration ---
PROJECT_ID = os.environ.get("PROJECT_ID", "my_project_id")
LOCATION = os.environ.get("LOCATION", "global")
DRY_RUN = os.environ.get("DRY_RUN", "False").lower() == "true"

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger()

def get_endpoint(location: str) -> str:
    """
    Returns the correct Discovery Engine endpoint based on location.

    Args:
        location (str): The location (e.g., "global", "us", "eu").

    Returns:
        str: The endpoint hostname.
    """
    if location == "global":
        return "discoveryengine.googleapis.com"
    return f"{location}-discoveryengine.googleapis.com"

# Functions for discovering default license and listing user licenses 
def get_session():
    """Authenticates and returns an authorized session."""
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return AuthorizedSession(credentials)

# Function to discover default license configuration (Gemini Enterprise) 
def discover_default_license(session):
    """Find the active defaultLicenseConfig.""" 
    endpoint = get_endpoint(LOCATION)
    url = f"https://{endpoint}/v1/projects/{PROJECT_ID}/locations/{LOCATION}/userStores/default_user_store"
    headers = {"X-Goog-User-Project": PROJECT_ID}

    logger.info("🔍 Step 1: Querying User Store for default license configuration...")
    response = session.get(url, headers=headers)

    if response.status_code != 200:
        logger.error(f"❌ Discovery Failed: {response.text}")
        response.raise_for_status()

    config = response.json().get("defaultLicenseConfig")
    if not config:
        raise ValueError("No defaultLicenseConfig found. Ensure Gemini Enterprise is active.")

    logger.info(f"✅ Found License Path: {config}")
    return config

# Function to list user licenses 
def list_user_licenses():
    """
    Lists user licenses from Google Cloud Discovery Engine API.
    Equivalent to the provided curl command.
    """
    # Create an authorized session to handle the token automatically
    authed_session = get_session()

    endpoint = get_endpoint(LOCATION)
    url = f"https://{endpoint}/v1/projects/{PROJECT_ID}/locations/{LOCATION}/userStores/default_user_store/userLicenses"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-User-Project": PROJECT_ID
    }

    try:
        response = authed_session.get(url, headers=headers)
        response.raise_for_status() # Raise an exception for HTTP errors
        return response
    except Exception as e:
        print(f"An error occurred: {e}")
        if 'response' in locals():
             print(f"Response content: {response.text}")
        return None

# Function to add a single user with a license configuration
def add_user(user_principal: str, license_config: str) -> bool:
    """
    Assigns a license configuration to a single user principal.

    Args:
        user_principal (str): The email address/principal of the user.
        license_config (str): The resource name of the license configuration.

    Returns:
        bool: True if the assignment was successful (or mock assigned in dry run), False otherwise.
    """
    session = get_session()
    endpoint = get_endpoint(LOCATION)
    url = f"https://{endpoint}/v1/projects/{PROJECT_ID}/locations/{LOCATION}/userStores/default_user_store:batchUpdateUserLicenses"
    headers = {"X-Goog-User-Project": PROJECT_ID, "Content-Type": "application/json"}

    payload = {
        "inlineSource": {
            "userLicenses": [
                {
                    "userPrincipal": user_principal,
                    "licenseConfig": license_config
                }
            ]
        }
    }

    logger.info(f"👤 Assigning license {license_config} to user {user_principal}...")

    if DRY_RUN:
        logger.info(f"[DRY RUN] Would have assigned license {license_config} to {user_principal}.")
        return True

    try:
        response = session.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            logger.info(f"✅ Successfully assigned license to {user_principal}.")
            
            # Assign Gemini Enterprise User role (roles/discoveryengine.agentspaceUser) to user
            logger.info(f"🔐 Assigning Gemini Enterprise User IAM role to {user_principal}...")
            try:
                iam_url_get = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT_ID}:getIamPolicy"
                iam_url_set = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT_ID}:setIamPolicy"
                
                # 1. Get current IAM Policy
                get_policy_res = session.post(iam_url_get, json={})
                get_policy_res.raise_for_status()
                policy = get_policy_res.json()
                
                # 2. Find or create binding for roles/discoveryengine.agentspaceUser
                role_to_bind = "roles/discoveryengine.agentspaceUser"
                member_to_add = f"user:{user_principal}"
                
                bindings = policy.setdefault("bindings", [])
                found_binding = None
                for binding in bindings:
                    if binding.get("role") == role_to_bind:
                        found_binding = binding
                        break
                        
                if found_binding:
                    members = found_binding.setdefault("members", [])
                    if member_to_add not in members:
                        members.append(member_to_add)
                        logger.info(f"Adding {member_to_add} to existing {role_to_bind} role binding.")
                    else:
                        logger.info(f"{member_to_add} is already bound to {role_to_bind}.")
                else:
                    logger.info(f"Creating new binding for {role_to_bind} with {member_to_add}.")
                    bindings.append({
                        "role": role_to_bind,
                        "members": [member_to_add]
                    })
                    
                # 3. Save the updated IAM Policy
                set_policy_res = session.post(iam_url_set, json={"policy": policy})
                set_policy_res.raise_for_status()
                logger.info(f"✅ Successfully assigned IAM role {role_to_bind} to {user_principal}.")
                return True
                
            except Exception as iam_error:
                logger.error(f"❌ Failed to assign IAM role: {iam_error}")
                if 'set_policy_res' in locals():
                    logger.error(f"Detail: {set_policy_res.text}")
                elif 'get_policy_res' in locals():
                    logger.error(f"Detail: {get_policy_res.text}")
                return False
        else:
            logger.error(f"❌ Failed to assign license to {user_principal}: {response.status_code}")
            logger.error(f"Detail: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Error during license assignment to {user_principal}: {e}")
        return False

if __name__ == "__main__":
    session = get_session()
    email_address = os.environ.get("EMAIL_ADDRESS", "eric@mycompany.com")
    
    # 1. Discover Default License
    try:
        default_license = discover_default_license(session)
    except Exception as e:
        logger.warning(f"Could not discover default license: {e}")

    # 2. Add user with license configuration 
    add_user(email_address, default_license)