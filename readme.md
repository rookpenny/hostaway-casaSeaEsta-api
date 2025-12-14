# Hostaway CasaSeaEsta API Documentation

Welcome to the **Hostaway CasaSeaEsta API**. This API powers the *Sandy* virtual concierge for vacation rentals, managing property data, reservations, guest communication, and optional upgrades. The backend is built with **FastAPI** and uses a PostgreSQL database via **SQLAlchemy**. This document provides an overview of the project structure and how to run it locally.

## Overview

| Key component | Purpose |
| :---- | :---- |
| **FastAPI** | Framework used to define HTTP endpoints for properties, reservations, guides, upgrades, chats, admin interfaces, and health checks. |
| **SQLAlchemy models** | Define data structures for PMCs (Property Management Companies), Properties, Reservations, Guides, Upgrades, ChatSessions, and ChatMessages. Relationships enforce uniqueness per property and per PMC. |
| **Hostaway integration** | Utilities in utils/hostaway.py handle authentication and data fetching from Hostaway (property overviews, reservations, guest phone verification). |
| **Stripe integration** | The /properties/{property\_id}/upgrades/{upgrade\_id}/checkout endpoint creates Stripe Checkout sessions so guests can purchase optional upgrades. |
| **OpenAI integration** | Chat endpoints use OpenAI’s API to generate helpful, friendly responses tailored to each property. System prompts include property details and house rules. |
| **APScheduler** | A background job runs regularly to sync PMC data and reservations. |
| **Templates & static files** | Jinja2 templates render guest and admin UIs, while static files (CSS/JS/images) are served from the static directory. |

## Project structure

.  
├── main.py                 \# Application entrypoint; sets up routers, middleware, and FastAPI app  
├── models.py               \# SQLAlchemy model definitions  
├── routes/                 \# Individual route modules (admin, PMC signup/auth, sync, Stripe webhook, etc.)  
├── utils/                  \# Helper modules for PMS access, Hostaway API, Airtable, billing, etc.  
├── templates/              \# Jinja2 templates for guest and admin pages  
├── static/                 \# CSS, JavaScript, images, and API docs (openapi.yaml)  
└── ...

### main.py

* Configures session middleware, CORS, and mounts static files and templates. Only requests from the production origin are allowed by default[\[1\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L69-L86).

* Includes routers from routes/admin.py, routes/pmc\_signup.py, routes/stripe\_webhook.py, utils/prearrival.py and others[\[2\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L52-L64).

* Provides basic endpoints for health checks (/, /health), property guides, manual sync, chat, verification, and checkout[\[3\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L149-L161)[\[4\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L764-L834).

* Integrates the OpenAI client to power the chat AI.[\[5\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L215-L228).

### models.py

Defines the core database tables using SQLAlchemy. Highlights include:

* **PMC**: property management company with billing status and PMS credentials[\[6\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L167-L201).

* **Property**: ties a property to a PMC; tracks provider, external IDs, and a sandy\_enabled flag for enabling the concierge[\[7\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L205-L244).

* **Reservation**: stores guest name, phone, arrival/departure dates, and references the property[\[8\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L116-L139).

* **Upgrade**: optional add‑ons guests can buy; includes price, currency, Stripe price ID, and a slug unique per property[\[9\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L249-L279).

* **ChatSession** and **ChatMessage**: record conversations between guests and the AI concierge[\[10\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L282-L341).

### utils/hostaway.py

Provides helper functions for interacting with Hostaway’s API:

* get\_token\_for\_pmc(client\_id, client\_secret): obtains an access token for a specific PMC[\[11\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py#L20-L46).

* get\_listing\_overview(listing\_id, client\_id, client\_secret): fetches the hero image, address, and city for a property[\[12\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py#L167-L210).

* get\_upcoming\_phone\_for\_listing(listing\_id, client\_id, client\_secret): returns the last four digits of the guest’s phone number and reservation details for verification[\[13\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py#L215-L323).

## Running the application locally

1. **Clone the repository**: Make sure you have access to the private repo.

git clone https://github.com/rookpenny/hostaway-casaSeaEsta-api.git  
cd hostaway-casaSeaEsta-api

1. **Set up a virtual environment** (optional but recommended):

python3 \-m venv venv  
source venv/bin/activate  
pip install \-r requirements.txt

1. **Set environment variables**: Create a .env file or set the following variables in your shell:

2. DATABASE\_URL: PostgreSQL connection string (e.g., postgresql+psycopg2://user:password@host:port/dbname)

3. STRIPE\_SECRET\_KEY: Stripe secret key for processing payments[\[14\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L11-L12).

4. OPENAI\_API\_KEY: OpenAI API key for chat functionality[\[15\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L50-L52).

5. SESSION\_SECRET: Secret key for session middleware[\[16\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L73-L78).

6. HOSTAWAY\_CLIENT\_ID and HOSTAWAY\_CLIENT\_SECRET: Hostaway credentials for your PMC[\[11\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py#L20-L46).

7. Additional Hostaway or PMS variables if needed (see utils/hostaway.py).

8. **Run database migrations**: If you have Alembic scripts, run them; otherwise, create the tables manually in PostgreSQL according to models.py.

9. **Start the development server**:

uvicorn main:app \--host 0.0.0.0 \--port 8000 \--reload

1. **Access the API**: Navigate to http://localhost:8000 to see the root response. The OpenAPI docs are available at /docs.

## Future improvements

* **Documentation completeness**: This initial file provides an overview; further sections could include detailed endpoint descriptions, examples, and deployment instructions.

* **Testing**: Add unit and integration tests for critical flows such as reservation syncing, verification, and chat.

* **CI/CD**: Configure automated testing and deployment pipelines (e.g., GitHub Actions).

---

Feel free to expand on this document. Add sections for deployment (e.g., Render.com config), add diagrams for data flow, or include deeper descriptions of each router and utility module.

---

[\[1\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L69-L86) [\[2\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L52-L64) [\[3\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L149-L161) [\[4\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L764-L834) [\[5\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L215-L228) [\[14\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L11-L12) [\[15\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L50-L52) [\[16\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py#L73-L78) main.py

[https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/main.py)

[\[6\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L167-L201) [\[7\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L205-L244) [\[8\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L116-L139) [\[9\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L249-L279) [\[10\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py#L282-L341) models.py

[https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/models.py)

[\[11\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py#L20-L46) [\[12\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py#L167-L210) [\[13\]](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py#L215-L323) hostaway.py

[https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py](https://github.com/rookpenny/hostaway-casaSeaEsta-api/blob/main/utils/hostaway.py)