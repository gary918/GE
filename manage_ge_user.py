import logging
import os
import sys
from typing import Any, Optional

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
def list_user_licenses(user_principal: Optional[str] = None) -> Any:
    """
    Lists user licenses from Google Cloud Discovery Engine API.
    Equivalent to the provided curl command.

    Args:
        user_principal (str, optional): If provided, filters and lists licenses assigned to this specific user.

    Returns:
        Any: The response object from the API request if user_principal is None,
        or a list of license configurations assigned to the user_principal.
    """
    # Create an authorized session to handle the token automatically
    authed_session = get_session()

    endpoint = get_endpoint(LOCATION)
    url = f"https://{endpoint}/v1/projects/{PROJECT_ID}/locations/{LOCATION}/userStores/default_user_store/userLicenses"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-User-Project": PROJECT_ID,
    }

    try:
        response = authed_session.get(url, headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors

        if user_principal is not None:
            data = response.json()
            user_licenses = data.get("userLicenses", [])
            assigned = [
                ul
                for ul in user_licenses
                if ul.get("userPrincipal") == user_principal
            ]
            logger.info(f"📋 Licenses assigned to {user_principal}:")
            if not assigned:
                logger.info("No licenses assigned.")
            else:
                for lic in assigned:
                    logger.info(f" - {lic.get('licenseConfig')}")
            return assigned

        return response
    except Exception as e:
        print(f"An error occurred: {e}")
        if "response" in locals():
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


# Function to delete a user and unassign their license configuration
def delete_user(user_principal: str) -> bool:
    """
    Checks if the user has been assigned to a Gemini Enterprise license.
    If yes, unassigns the license, deletes the user from Gemini Enterprise,
    and removes the user's role of "roles/discoveryengine.agentspaceUser".
    If no, logs that the user has not been assigned.

    Args:
        user_principal (str): The email address/principal of the user.

    Returns:
        bool: True if the unassignment and deletion were successful (or not assigned), False otherwise.
    """
    logger.info(f"🔍 Checking if user {user_principal} is assigned to a Gemini Enterprise license...")
    res = list_user_licenses(user_principal)  # Check if user has a license
    is_assigned = False
    assigned_license = ""

    if res:
        for ul in res:
            if ul.get("userPrincipal") == user_principal:
                is_assigned = True
                assigned_license = ul.get("licenseConfig", "")
                break

    if not is_assigned:
        logger.info("This user has not been assigned to any Gemini Enterprise license.")
        return True

    session = get_session()
    endpoint = get_endpoint(LOCATION)
    url = f"https://{endpoint}/v1/projects/{PROJECT_ID}/locations/{LOCATION}/userStores/default_user_store:batchUpdateUserLicenses"
    headers = {"X-Goog-User-Project": PROJECT_ID, "Content-Type": "application/json"}

    payload = {
        "inlineSource": {
            "userLicenses": [
                {
                    "userPrincipal": user_principal,
                    "licenseConfig": ""
                }
            ]
        },
        "deleteUnassignedUserLicenses": True
    }

    logger.info(f"👤 Unassigning license {assigned_license} for user {user_principal} and deleting user from Gemini Enterprise...")

    if DRY_RUN:
        logger.info(f"[DRY RUN] Would have unassigned license for {user_principal} and deleted user.")
        return True

    try:
        response = session.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            logger.info(f"✅ Successfully unassigned license and deleted user {user_principal}.")
            
            # Remove Gemini Enterprise User role (roles/discoveryengine.agentspaceUser) from user
            logger.info(f"🔐 Removing Gemini Enterprise User IAM role from {user_principal}...")
            try:
                iam_url_get = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT_ID}:getIamPolicy"
                iam_url_set = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT_ID}:setIamPolicy"
                
                # 1. Get current IAM Policy
                get_policy_res = session.post(iam_url_get, json={})
                get_policy_res.raise_for_status()
                policy = get_policy_res.json()
                
                # 2. Find and remove member from binding for roles/discoveryengine.agentspaceUser
                role_to_unbind = "roles/discoveryengine.agentspaceUser"
                member_to_remove = f"user:{user_principal}"
                
                bindings = policy.get("bindings", [])
                binding_modified = False
                bindings_to_keep = []
                
                for binding in bindings:
                    if binding.get("role") == role_to_unbind:
                        members = binding.get("members", [])
                        if member_to_remove in members:
                            members.remove(member_to_remove)
                            binding_modified = True
                            logger.info(f"Removing {member_to_remove} from {role_to_unbind} role binding.")
                        if members:
                            bindings_to_keep.append(binding)
                    else:
                        bindings_to_keep.append(binding)
                        
                if binding_modified:
                    policy["bindings"] = bindings_to_keep
                    # 3. Save the updated IAM Policy
                    set_policy_res = session.post(iam_url_set, json={"policy": policy})
                    set_policy_res.raise_for_status()
                    logger.info(f"✅ Successfully removed IAM role {role_to_unbind} from {user_principal}.")
                else:
                    logger.info(f"{member_to_remove} was not found in {role_to_unbind} role binding.")
                    
                return True
                
            except Exception as iam_error:
                logger.error(f"❌ Failed to remove IAM role: {iam_error}")
                if 'set_policy_res' in locals():
                    logger.error(f"Detail: {set_policy_res.text}")
                elif 'get_policy_res' in locals():
                    logger.error(f"Detail: {get_policy_res.text}")
                return False
        else:
            logger.error(f"❌ Failed to unassign license and delete user {user_principal}: {response.status_code}")
            logger.error(f"Detail: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Error during license unassignment and deletion for {user_principal}: {e}")
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
    # add_user(email_address, default_license)

    # 3. Delete user with license configuration 
    delete_user(email_address)