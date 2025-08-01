from dataclasses import dataclass
import json
from patchright.sync_api import sync_playwright, Page, Cookie
from urllib.parse import urlencode, urlparse, parse_qs, unquote
from uuid import uuid4
from typing import Dict, List, cast, Tuple
import yaml
import time
import os
import sys
from logging import getLogger, INFO, StreamHandler
from random import randint

logger = getLogger()
logger.setLevel(INFO)
logger.addHandler(StreamHandler(sys.stdout))
info = logger.info


launch_options = {
    "headless": False,
}

@dataclass
class AuthSession:
    """
    Cookie that is saved to avoid 2FA again (bns-auth-saved-users)
    """
    multi_user_cookie: Cookie 

    """
    2sv cookie
    """
    two_sv_cookie: Cookie

    """
    RSID that was used to authenticate the session
    """
    used_rsid: str

    """
    Final auth token that is used to authenticate the session
    """
    auth_token: str

@dataclass
class ClientSession:
    """
    Session ID cookie that will be used to call API
    """
    session_id_cookie: Cookie

    """
    Bypass Akamai cookies (incudes bm_sv, bm_sz)
    """
    bypass_akamai: Dict[str, Cookie]

@dataclass
class Session:
    auth_session: AuthSession
    client_session: ClientSession

class ScotiaClient:
    def __init__(self, credentials_file, session_file="scotia_session.json"):
        # Magic found on the JS client
        self.clientId = "4ecf7e39-be56-4a66-816c-13cb94e62da5"

        if os.path.exists(credentials_file):
            with open(credentials_file, "r") as f:
                cred_file = yaml.safe_load(f)
                scotia_block = cred_file.get("scotia")
                if scotia_block:
                    self.credentials = (
                        scotia_block.get("username"),
                        scotia_block.get("password")
                    )
                else:
                    raise ValueError(f"Missing scotia block in {credentials_file}")
        else:
            raise ValueError("Missing credentials file")
        
        self.session_file = session_file
        self.restore_session()

    def get_credentials(self):
        user, passwd = self.credentials
        # decode the masked ID from the multi-user cookie to set the proper user
        if self.session:
            encoded_value = self.session.auth_session.multi_user_cookie["value"]
            decoded_value = json.loads(unquote(encoded_value))
            user = decoded_value[0]["maskedId"]

        return user, passwd

    def handle_password_challenge(self, challenge):
        user, password = self.get_credentials()
        password_jwt = self.encode_jwt(
            {"alg": "none", "typ": "JWT"},
            {"rememberme": True, "pass": password, "login": user}
        )
        challenge["value"] = password_jwt + "."
        return challenge
    
    def restore_session(self):
        """
        Restore the session from a file
        """
        if not os.path.exists(self.session_file):
            info("Session file does not exist, cannot load back session config")
            self.session: Session | None = None
            self.rsid = "web_" + str(uuid4())
            return
        
        with open(self.session_file, "r") as f:
            session_data = json.load(f)
            self.session = Session(
                auth_session=AuthSession(
                    multi_user_cookie=session_data["auth_session"]["multi_user_cookie"],
                    used_rsid=session_data["auth_session"]["used_rsid"],
                    auth_token=session_data["auth_session"]["auth_token"],
                    two_sv_cookie=session_data["auth_session"]["two_sv_cookie"]
                ),
                client_session=ClientSession(
                    session_id_cookie=session_data["client_session"]["session_id_cookie"],
                    bypass_akamai=session_data["client_session"]["bypass_akamai"]
                )
            )
        
        # Unique session identifier for auth
        self.rsid = self.session.auth_session.used_rsid
        info(f"Restored session from file")

    def populate_cookies_from_session(self, page: Page):
        """
        Populate the cookies from the session
        """
        if not self.session:
            info("No session to populate cookies from")
            return
        
        # Set the cookies in the page context
        cookies_to_restore = [
            self.session.auth_session.multi_user_cookie,
            self.session.client_session.session_id_cookie,
            *[ v for _, v in self.session.client_session.bypass_akamai.items() ]
        ]
        page.context.add_cookies(cookies=cookies_to_restore)
        info(f"Restored {len(cookies_to_restore)} cookies to page context")

    @staticmethod
    def encode_jwt(header, payload):
        import base64
        import json
        header_base64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        payload_base64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return f"{header_base64}.{payload_base64}"
    
    def build_authentications_header(self, pageAuthUrl, authToken=None, webTrackId=None, additional_headers = {}) -> Dict[str, str]:
        oauth_headers: Dict[str, str] = {}
        web_track_id: Dict[str, str] = {}
        if authToken:
            oauth_headers["x-auth-token"] = authToken
        else:
            url = urlparse(pageAuthUrl)
            query_params = parse_qs(url.query)
            oauth_key = query_params.get("oauth_key", [])

            if not oauth_key:
                raise ValueError(f"Missing oauth_key in URL {pageAuthUrl} with query {query_params}")
            oauth_key = str(oauth_key[0])

            oauth_key_signature = query_params.get("oauth_key_signature", [])
            if not oauth_key_signature:
                raise ValueError(f"Missing oauth_key_signature in URL: {pageAuthUrl} with query {query_params}")
            oauth_key_signature = oauth_key_signature[0]

            oauth_headers["x-oauth-key"] = oauth_key
            oauth_headers["x-oauth-signed-key"] = oauth_key_signature
        
        if webTrackId:
            web_track_id["x-web-track-id"] = webTrackId
        
        username, _ = self.get_credentials()
        deviceType = "Phoenix"

        headers: Dict[str, str] = {
            **self.build_client_headers(),
            "x-rsi": self.rsid,
            "x-device-type": deviceType,
            "x-login-id": username,
            "x-remember-user": "true",
            **oauth_headers,
            **web_track_id,
            **additional_headers
        }
        return headers
    
    def build_client_headers(self):
        return {
            "Content-Type": "application/json",
            "x-client-id": self.clientId,
        }

    def fetch_auth_saved_users(self, page: Page) -> Tuple[Cookie, Cookie]:
        """
        Fetch the auth saved users from the response
        """
        bns_auth_saved_users: Cookie | None = next(
            (cookie for cookie in page.context.cookies() if "name" in cookie and "bns-auth-saved-users" in cookie["name"]),
            None
        )
        if not bns_auth_saved_users:
            raise Exception("Missing set-cookie in current context, cookies: " + str(page.context.cookies()))
    
        # decode the bns-auth-saved-users cookie (its url encoded)
        bns_auth_saved_users_val = bns_auth_saved_users.get("value")
        if not bns_auth_saved_users_val:
            raise Exception("Missing value in bns-auth-saved-users cookie")
        
        decoded_bns_auth_saved_users = unquote(bns_auth_saved_users_val)
        # decode the cookie value
        decoded_bns_auth_saved_users = json.loads(decoded_bns_auth_saved_users)

        # find the masked id
        masked_id = next(
            (user["maskedId"] for user in decoded_bns_auth_saved_users if user["maskedId"]), None
        )
        if not masked_id:
            raise Exception("Missing masked_id in bns-auth-saved-users")

        web_track_id = next(
            (user["webTrackId"] for user in decoded_bns_auth_saved_users if user["webTrackId"]), None
        )
        if not web_track_id:
            raise Exception("Missing webTrackId in bns-auth-saved-users")
        info(f"Masked ID: {masked_id}, Web Track ID: {web_track_id}")

        # invoke the mult-user api
        multi_user_url = f"https://auth.scotiaonline.scotiabank.com/v2/authentications/multi-user/{masked_id}"
        multi_user_headers = {
            "x-web-track-id": web_track_id,
            **self.build_client_headers()
        }

        response = page.context.request.post(multi_user_url, headers=multi_user_headers)
        if response.status != 204:
            raise Exception(f"Expected 204, got {response.status} body: {response.text()}")
        
        # decode the bns-auth-saved-users cookie again
        second_round_cookies: List[Cookie] = page.context.cookies()
        bns_auth_saved_user_cookie = next(
            (cookie for cookie in second_round_cookies if "name" in cookie and "bns-auth-saved-users" in cookie["name"]),
            None
        )
        two_sv_cookie = next(
            (cookie for cookie in second_round_cookies if "name" in cookie and "2sv" in cookie["name"]),
            None
        )
        if not bns_auth_saved_user_cookie:
            raise Exception("Missing set-cookie in current context, cookies: " + str(page.context.cookies()))
        
        if not two_sv_cookie:
            raise Exception("Missing 2sv cookie in current context, cookies: " + str(page.context.cookies()))

        info("Multi-user API responded with valid cookie")
        return bns_auth_saved_user_cookie, two_sv_cookie

    def save_session(self):
        """
        Save the session to a file
        """
        if not self.session:
            raise ValueError("Session is not set")
        
        with open(self.session_file, "w") as f:
            json.dump({
                "auth_session": {
                    "multi_user_cookie": self.session.auth_session.multi_user_cookie,
                    "used_rsid": self.session.auth_session.used_rsid,
                    "auth_token": self.session.auth_session.auth_token
                },
                "client_session": {
                    "session_id_cookie": self.session.client_session.session_id_cookie,
                    "bypass_akamai": self.session.client_session.bypass_akamai
                }
            }, f)
        info(f"Session saved to '{self.session_file}'")

    def sleep(self, seconds):
        """
        Sleep for the given number of seconds
        """
        for i in range(seconds):
            time.sleep(1)
            print(".", end="")
            sys.stdout.flush()
        print()

    def collect_session_client_cookies(self, page: Page) -> ClientSession:
        """
        Update the session with the current page context
        """
        info("Sleeping for 5 seconds to let akamai be happy")
        self.sleep(5)

        # Get the cookies from the page context
        page_cookies = page.context.cookies()
        # Check if the session ID cookie is present
        session_id_cookie = next(
            (cookie for cookie in page_cookies if "name" in cookie and "session-id" in cookie["name"]),
            None
        )
        if not session_id_cookie:
            raise Exception("Missing session-id cookie in current context, cookies: " + str(page_cookies))
        
        # Check if the bypass_akamai cookies are present
        bypass_akamai_cookies = [
            cookie for cookie in page_cookies if "name" in cookie and cookie["name"] in ["bm_sv", "bm_sz", "_abck", 
                                                                    "ak_bmsc", "AKA_A2", "bm_mi", "bmuid"]
        ]
        if not bypass_akamai_cookies:
            raise Exception("Missing bypass_akamai cookies in current context, cookies: " + str(page_cookies))
        info(f"Collected {len(bypass_akamai_cookies)} Akamai cookies from current context")
        return ClientSession(
            session_id_cookie=session_id_cookie,
            bypass_akamai={
                cookie["name"]: cookie for cookie in bypass_akamai_cookies
            }
        )

    def authenticate(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_options)
            page = browser.new_page()

            self.populate_cookies_from_session(page)

            summary_url = "https://secure.scotiabank.com/api/accounts/summary"
            page.goto(summary_url)

            self.sleep(1)

            if not page.url.startswith("https://auth.scotiaonline.scotiabank.com/"):
                info(f"Already authenticated, skipping. Current URL: {page.url}")
                self.session.client_session = self.collect_session_client_cookies(page)
                self.save_session()
                return

            page.locator("fieldset input#password-input").click()
            page.locator("fieldset input#password-input").fill(self.credentials[1])

            self.sleep(10)

            auth_url = "https://auth.scotiaonline.scotiabank.com/v2/authentications"
            empty_auth_req = {
                "authenticator_key": None,
                "user_key": None
            }

            response = page.context.request.post(
                auth_url, 
                data=json.dumps(empty_auth_req),
                headers=self.build_authentications_header(page.url))
            
            info("--------- STEP 1 ---------")
            info(f"Authentication response: {response.text()}")
            
            if response.status != 206:
                raise Exception(f"Expected 200, got {response.status}")
            
            auth_res = response.json()
            auth_token = response.headers.get("x-auth-token")

            password_challenge = None
            mfa_nonce = None
            two_sv_token = None

            for challenge in auth_res["challenges"]:
                if challenge["type"] == "PASSWORD":
                    password_challenge = self.handle_password_challenge(challenge)
                elif challenge["type"] == "MFA_NONCE":
                    mfa_nonce = challenge
                    mfa_nonce["value"] = "shouldProvidedByPnx"
                elif challenge["type"] == "TWO_SV_TOKEN":
                    two_sv_token = challenge
                    two_sv_token["value"] = "shouldProvidedByPnx"

            if not all([password_challenge, mfa_nonce, two_sv_token]):
                raise Exception("Missing required challenges")
            
            
            solved_challenges = cast(List[dict], [password_challenge, mfa_nonce, two_sv_token])
            auth_url_with_key = f"{auth_url}/{auth_res['key']}"
            info("--------- STEP 2 ---------")
            info(f"Authentication URL: {auth_url_with_key}")
            info(f"Authentication solved challenges: {[ch['type'] for ch in solved_challenges]}")

            response = page.request.post(auth_url_with_key, 
                                         data=json.dumps(solved_challenges),
                                         headers=self.build_authentications_header(page.url, auth_token))
            auth_with_code = None
            web_track_id = None
            additional_headers = {}

            info("--------- STEP 3 ---------")
            while True:
                if response.status > 299:
                    if response.status == 401:
                        info("Authentication failed, retrying...")
                        info(f"Response headers: {response.headers}")
                        info(f"Authentication response: {response.text()}")
                        self.sleep(5)
                        response = page.request.post(auth_url_with_key, 
                                                     data=json.dumps(solved_challenges),
                                                     headers=self.build_authentications_header(page.url, auth_token, 
                                                                                               web_track_id, additional_headers=additional_headers))
                        continue
                    raise Exception(f"Expected 2xx, got {response.status}")

                auth_with_code = response.json()
                web_track_id = response.headers.get("x-web-track-id")
                
                auth_token = response.headers.get("x-auth-token")
                if auth_with_code.get("redirect_uri"):
                    info(f"Redirect URL: {auth_with_code['redirect_uri']}")
                    break
                
                info("Authentication challenges:", [ch["type"] for ch in auth_with_code["challenges"]])

                selected_challenge = next(
                    (ch for ch in auth_with_code["challenges"] if ch["type"] == "POLLING"), None
                )
                if not selected_challenge:
                    # this might be the first request so we want to indicate that we want
                    # two sv (TODO there are two challenges here TWO_SV and TWO_SV_TOKEN)
                    # my intuition is that we can solve TWO_SV_TOKEN by sending the signed token from
                    # the second auth call (the /multi-user)
                    selected_challenge = next(
                        (ch for ch in auth_with_code["challenges"] if ch["type"] == "TWO_SV"), None
                    )
                if not selected_challenge:
                    raise Exception("Missing polling challenge")
                
                info(f"Selected challenge: {selected_challenge['type']}")

                additional_headers = {"x-bff-action": "tmp-cookie-2sv-token"} if selected_challenge["type"] == "POLLING" else {}

                selected_challenge["value"] = None
                response = page.context.request.post(auth_url_with_key, 
                                             data=json.dumps([selected_challenge]),
                                             headers=self.build_authentications_header(page.url, auth_token, web_track_id, 
                                                                                       additional_headers=additional_headers))
                self.sleep(5)

            if not auth_token:
                raise Exception("Missing auth token in response")
            info("--------- STEP 4 ---------")
            bns_auth_saved_users, two_sv_cookie = self.fetch_auth_saved_users(page)

            redirect_uri = auth_with_code["redirect_uri"]
            parsed_uri = urlparse(redirect_uri)
            query_params = parse_qs(parsed_uri.query)
            query_params["code"] = auth_with_code["auth_code"]
            query_params["state"] = auth_with_code["state"]
            query_params["log_id"] = [auth_token]

            final_url = f"{parsed_uri.scheme}://{parsed_uri.netloc}{parsed_uri.path}?{urlencode(query_params, doseq=True)}"
            info(f"Executing final url to '{parsed_uri.netloc}'")
            final_response = page.context.request.get(final_url)
            if final_response.status != 200:
                raise Exception(f"Expected 200, got {final_response.status} body:{final_response.text()}")
            
            # Save the session
            self.session = Session(
                auth_session=AuthSession(
                    multi_user_cookie=bns_auth_saved_users,
                    used_rsid=self.rsid,
                    auth_token=auth_token,
                    two_sv_cookie=two_sv_cookie
                ),
                client_session=self.collect_session_client_cookies(page)
            )
            
            info("Authentication successful")

            # Save the session to a file
            self.save_session()
            browser.close()

client = ScotiaClient("config.yaml")
client.authenticate()
info("Authentication completed")