# routes/stripe_connect.py
import os, secrets
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import PMCIntegration
from routes.admin import get_user_role_and_scope

router = APIRouter()

STRIPE_CLIENT_ID = (os.getenv("STRIPE_CLIENT_ID") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")
STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()

stripe.api_key = STRIPE_SECRET_KEY


def _require_env():
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


@router.post("/admin/integrations/stripe/connect")
def stripe_oauth_start(request: Request, db: Session = Depends(get_db)):
    require_env()

    # however you identify the logged-in PMC:
    pmc_id = request.session.get("pmc_id")
    if not pmc_id:
        raise HTTPException(401, "Not authenticated")

    # CSRF state
    state = secrets.token_urlsafe(24)
    request.session["stripe_oauth_state"] = state

    redirect_uri = f"{APP_BASE_URL}/admin/integrations/stripe/oauth/callback"

    url = (
        "https://connect.stripe.com/oauth/authorize"
        f"?response_type=code"
        f"&client_id={STRIPE_CLIENT_ID}"
        f"&scope=read_write"
        f"&state={state}"
        f"&redirect_uri={redirect_uri}"
    )

    return {"url": url}


@router.get("/admin/integrations/stripe/oauth/callback")
def stripe_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(500, "Missing STRIPE_SECRET_KEY")
    stripe.api_key = STRIPE_SECRET_KEY

    pmc_id = request.session.get("pmc_id")
    if not pmc_id:
        raise HTTPException(401, "Not authenticated")

    # user cancelled or Stripe error
    if error:
        return HTMLResponse(
            f"""
            <script>
              if (window.opener) {{
                window.opener.postMessage({{ type: "stripe_oauth_complete", ok: false }}, "*");
                window.close();
              }} else {{
                window.location.href = "/admin";
              }}
            </script>
            """,
            status_code=200,
        )

    # CSRF check
    expected = request.session.get("stripe_oauth_state")
    if not expected or not state or state != expected:
        raise HTTPException(400, "Invalid OAuth state")

    if not code:
        raise HTTPException(400, "Missing code")

    token = stripe.OAuth.token(
        grant_type="authorization_code",
        code=code,
    )

    acct_id = token.get("stripe_user_id")  # ✅ connected account id (acct_...)
    if not acct_id:
        raise HTTPException(400, "Stripe did not return stripe_user_id")

    # upsert integration row
    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
        .first()
    )
    if not integ:
        integ = PMCIntegration(pmc_id=pmc_id, provider="stripe_connect")
        db.add(integ)

    integ.account_id = acct_id
    integ.is_connected = True

    # Optional: store access token if you plan to call Stripe *as the connected account*
    # integ.access_token = token.get("access_token")
    # integ.refresh_token = token.get("refresh_token")

    db.commit()

    # Tell opener to refresh status + close popup
    return HTMLResponse(
        """
        <script>
          if (window.opener) {
            window.opener.postMessage({ type: "stripe_oauth_complete", ok: true }, "*");
            window.close();
          } else {
            window.location.href = "/admin";
          }
        </script>
        """,
        status_code=200,
    )


@router.post("/admin/integrations/stripe/disconnect")
def stripe_disconnect(request: Request, db: Session = Depends(get_db)):
    pmc_id = request.session.get("pmc_id")
    if not pmc_id:
        raise HTTPException(401, "Not authenticated")

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
        .first()
    )
    if not integ:
        return {"ok": True}

    # Optional revoke call if you stored access_token:
    # stripe.OAuth.deauthorize(client_id=STRIPE_CLIENT_ID, stripe_user_id=integ.account_id)

    integ.account_id = None
    integ.is_connected = False
    integ.access_token = None
    integ.refresh_token = None

    db.commit()
    return {"ok": True}

def require_pmc_scope(request: Request, db: Session):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)
    if user_role != "pmc" or not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC access required")

    return pmc_obj


@router.get("/admin/integrations/stripe/status")
def stripe_connect_status(request: Request, db: Session = Depends(get_db)):
    try:
        pmc_obj = require_pmc_scope(request, db)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )

    if not integ or not integ.account_id:
        return {"connected": False}

    return {
        "connected": True,
        "account_id": integ.account_id,
        "is_connected": bool(integ.is_connected),
    }


@router.post("/admin/integrations/stripe/connect")
def stripe_connect_start(request: Request, db: Session = Depends(get_db)):
    try:
        _require_env()
        pmc_obj = require_pmc_scope(request, db)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )

    if not integ:
        integ = PMCIntegration(
            pmc_id=pmc_obj.id,
            provider="stripe_connect",
            is_connected=False,
        )
        db.add(integ)
        db.commit()
        db.refresh(integ)

    # Create the connected account once
    if not integ.account_id:
        try:
            acct = stripe.Account.create(
                type="express",
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
                metadata={"pmc_id": str(pmc_obj.id)},
            )
            integ.account_id = acct["id"]
            integ.is_connected = False
            db.commit()
        except Exception as e:
            db.rollback()
            return JSONResponse({"detail": f"Stripe account create failed: {str(e)}"}, status_code=500)

    # Create onboarding link
    try:
        link = stripe.AccountLink.create(
            account=integ.account_id,
            refresh_url=f"{APP_BASE_URL}/admin/dashboard?view=settings&tab=integrations",
            return_url=f"{APP_BASE_URL}/admin/integrations/stripe/callback?popup=1",
            type="account_onboarding",
        )
        return {"url": link["url"]}
    except Exception as e:
        return JSONResponse({"detail": f"Stripe account link failed: {str(e)}"}, status_code=500)


@router.get("/admin/integrations/stripe/callback")
def stripe_connect_callback(request: Request, db: Session = Depends(get_db), popup: int = 0):
    _require_env()
    pmc_obj = require_pmc_scope(request, db)

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )

    if not integ or not integ.account_id:
        if popup:
            return HTMLResponse("""
<!doctype html><html><body>
<script>
  try { if (window.opener) window.opener.location.reload(); } catch(e) {}
  setTimeout(function(){ window.close(); }, 250);
</script>
</body></html>
""")
        return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")

    # Validate account exists on Stripe, then mark connected in our DB
    try:
        stripe.Account.retrieve(integ.account_id)
    except Exception as e:
        if popup:
            return HTMLResponse(f"<pre>Stripe retrieve failed: {str(e)}</pre>")
        return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")

    integ.is_connected = True
    db.commit()

    if popup:
        return HTMLResponse("""
<!doctype html><html><body>
<script>
  try { if (window.opener) window.opener.location.reload(); } catch(e) {}
  setTimeout(function(){ window.close(); }, 250);
</script>
</body></html>
""")

    return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")


@router.post("/admin/integrations/stripe/disconnect")
def stripe_connect_disconnect(request: Request, db: Session = Depends(get_db)):
    pmc_obj = require_pmc_scope(request, db)

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )

    if not integ:
        return {"ok": True}

    # If you want "Remove connection" to truly remove it:
    integ.is_connected = False
    integ.account_id = None   # ✅ this is the big change

    db.commit()
    return {"ok": True}

