# Gemini Enterprise User Management Tool
This script is used to manage Gemini Enterprise user licenses and IAM roles. It can be used to add, delete users to Gemini Enterprise and IAM roles.

## Prerequisites
* Login Google Cloud by using `gcloud auth application-default login`
* Set up your Google Cloud project and enable the Discovery Engine API by following the instructions in the [Discovery Engine API quickstart](https://cloud.google.com/discovery-engine/docs/quickstart). 
* Assign the Discovery Engine Admin role (roles/discoveryengine.admin) to the user running the script.

## Usage
* Setup the python environment `python3 -m venv .venv`
* Install needed libs `pip install -r requirements.txt`
* Set enviroment variables in .env file
* Dry run (optional) `python3 manage_ge_user.py`
* Production run `python3 manage_ge_user.py`
