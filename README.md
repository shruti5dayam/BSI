# BSI — Bank Statement Intelligence

BSI is a production-oriented financial automation platform that transforms raw
bank statements into traceable accounting classifications, financial reports,
review workflows, and AI-assisted financial insights.

The platform is being developed from an existing Python and Streamlit prototype
into a modular FastAPI application backed by PostgreSQL.

---

## Product Goal

BSI automates the financial-processing workflow:

```text
Upload Bank Statements
        ↓
Validate Source Files
        ↓
Extract and Normalize Transactions
        ↓
Resolve Company, Brand, Store, and Bank Account
        ↓
Apply Approved Deterministic Rules
        ↓
Map Transactions to the Chart of Accounts
        ↓
Create Exceptions for Unresolved Transactions
        ↓
Generate Trial Balance
        ↓
Generate Profit and Loss
        ↓
Display Executive Dashboard
        ↓
Provide AI-Assisted Explanations and Recommendations
```

Accounting calculations and authoritative financial outputs remain deterministic
and auditable.

AI capabilities are advisory. They may suggest rules, accounts, or explanations,
but they must not silently change financial mappings or reports.

---

## Core Engineering Principles

1. Financial correctness comes before advanced AI.
2. Financial amounts use `Decimal`, not binary floating-point arithmetic.
3. Original bank-statement data is preserved unchanged.
4. Every derived transaction remains linked to its source file and source row.
5. Rules and Chart of Accounts data are versioned.
6. Completed financial outputs are reproducible.
7. AI recommendations require controlled human review.
8. Every workspace is isolated from other workspaces.
9. Streamlit and future frontends call the backend through APIs.
10. Core accounting logic does not depend on FastAPI, Streamlit, or PostgreSQL.

---

## Architecture

BSI begins as a modular monolith.

```text
Streamlit / Future React Frontend
                ↓ HTTP
              FastAPI
                ↓
        Application Services
                ↓
       Deterministic Domain Logic
                ↓
        Repository Interfaces
                ↓
Infrastructure Implementations
                ↓
 PostgreSQL / File Storage / Workers
```

### API Layer

Responsible for:

- Receiving HTTP requests
- Request and response validation
- Authentication
- Authorization
- Correlation IDs
- Calling application services
- Converting application errors into API responses

The API layer must not calculate Trial Balance or Profit and Loss values.

### Application Layer

Responsible for coordinating complete use cases such as:

- Uploading documents
- Starting a processing run
- Resolving an exception
- Approving a rule version
- Generating a report snapshot

### Domain Layer

Contains framework-independent business logic such as:

- Transaction normalization
- Payment and deposit direction
- Signed financial amounts
- Rule conditions
- AND and OR evaluation
- Rule ranking and conflict detection
- Chart of Accounts validation
- Trial Balance calculations
- Profit and Loss calculations

### Infrastructure Layer

Contains technical implementations such as:

- PostgreSQL
- SQLAlchemy repositories
- Alembic migrations
- File storage
- Background workers
- Embedding providers
- AI providers

---

## Current Development Phase

The project is currently in:

```text
Phase 0 — Baseline and Project Foundation
```

Current objectives:

- Establish the production project structure
- Preserve the existing financial baseline
- Configure testing and code-quality tools
- Create deterministic transaction models
- Replace `float` financial values with `Decimal`
- Add unit and financial golden tests
- Prepare for PostgreSQL, SQLAlchemy, Alembic, and FastAPI

---

## Project Structure

```text
BSI/
├── src/
│   └── bsi/
│       ├── api/
│       │   ├── dependencies/
│       │   ├── error_handlers/
│       │   └── v1/
│       │       ├── routers/
│       │       └── schemas/
│       │
│       ├── application/
│       │   ├── commands/
│       │   ├── interfaces/
│       │   ├── queries/
│       │   └── services/
│       │
│       ├── domain/
│       │   ├── coa/
│       │   ├── reporting/
│       │   ├── rules/
│       │   ├── shared/
│       │   └── transactions/
│       │
│       ├── infrastructure/
│       │   ├── database/
│       │   │   ├── models/
│       │   │   └── repositories/
│       │   ├── providers/
│       │   └── storage/
│       │
│       ├── security/
│       └── workers/
│
├── frontend/
│   └── streamlit/
│
├── tests/
│   ├── golden/
│   ├── integration/
│   │   ├── api/
│   │   └── repositories/
│   └── unit/
│       └── domain/
│           ├── coa/
│           ├── reporting/
│           ├── rules/
│           └── transactions/
│
├── migrations/
│   └── versions/
├── data/
│   └── samples/
├── scripts/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

---

## Technology Stack

### Backend

- Python
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- PostgreSQL
- Psycopg

### Financial Processing

- Python `Decimal`
- Pandas
- OpenPyXL
- Deterministic rule engine

### Frontend

- Streamlit
- Future React or Next.js frontend

### Testing and Quality

- Pytest
- Hypothesis
- Ruff
- Mypy
- Coverage.py

### Future Infrastructure

- Docker
- Azure Container Apps
- Azure Database for PostgreSQL
- Azure Blob Storage
- Azure Service Bus
- Azure Key Vault
- Azure Monitor

### Future AI Capabilities

- Embeddings
- PostgreSQL with pgvector
- Controlled LLM providers
- AI rule recommendations
- Financial explanations
- Natural-language financial assistant

---

## Local Development Setup

### 1. Clone the repository

```bash
git clone https://github.com/shruti5dayam/BSI.git
cd BSI
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
```

### 3. Activate the virtual environment

macOS or Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### 4. Install the project

```bash
python -m pip install -e ".[dev,ui]"
```

### 5. Verify the installation

```bash
python -c "import bsi; print('BSI package import successful')"
python -m pip check
```

---

## Development Commands

### Run tests

```bash
python -m pytest
```

### Run unit tests only

```bash
python -m pytest -m unit
```

### Run integration tests only

```bash
python -m pytest -m integration
```

### Run financial golden tests

```bash
python -m pytest -m golden
```

### Run Ruff checks

```bash
python -m ruff check .
```

### Format code

```bash
python -m ruff format .
```

### Run type checking

```bash
python -m mypy src
```

### Run dependency validation

```bash
python -m pip check
```

---

## Financial Data Rules

Do not commit:

- Real bank statements
- Client Chart of Accounts files
- Client rule files
- Generated financial reports
- API keys
- Passwords
- Database credentials
- Authentication tokens
- `.env` files
- Virtual environments

Approved synthetic or anonymized samples may be stored under:

```text
data/samples/
```

Any sample data added to Git must be reviewed before committing.

---

## Git Workflow

The stable branch is:

```text
main
```

Development work should occur on feature branches.

Example:

```bash
git checkout -b feature/phase-1-transaction-domain
```

Recommended commit-message examples:

```text
chore: configure project foundation
feat: add transaction direction model
feat: implement deterministic rule evaluator
test: add transaction amount unit tests
fix: prevent ambiguous payment and deposit values
refactor: separate rule ranking from evaluation
docs: update processing architecture
```

---

## Testing Strategy

BSI uses several levels of testing.

### Unit Tests

Test isolated financial rules without a database or network connection.

Examples:

- Amount parsing
- Transaction-direction detection
- Signed-amount calculation
- Rule operators
- Rule ranking
- COA validation
- P&L subtotal calculations

### Integration Tests

Test real technical components working together.

Examples:

- SQLAlchemy with PostgreSQL
- FastAPI with application services
- Repository workspace filtering
- Alembic migrations
- File-storage adapters

### Golden Financial Tests

Golden tests compare approved source inputs to exact expected outputs.

```text
Approved Input Files
        ↓
Deterministic Pipeline
        ↓
Expected Mapping Results
        ↓
Expected Trial Balance
        ↓
Expected Profit and Loss
```

A financial result must not change silently during refactoring.

---

## Security Principles

- Deny access by default.
- Enforce permissions in the backend.
- Apply `workspace_id` filtering to tenant-owned data.
- Never trust a workspace identifier only because the frontend supplied it.
- Preserve immutable audit history.
- Mask bank-account numbers.
- Store secrets outside source code.
- Do not expose internal storage paths.
- Do not allow AI services to activate accounting rules or modify reports.

---

## Current Sample Baseline

The existing prototype was tested with one approved development sample for store
DD13.

Baseline values are preserved for comparison during refactoring, but they do not
become permanent accounting expectations until reviewed and approved as golden
test outputs.

Current prototype baseline:

```text
Transactions:             2,501
Rules:                       55
Chart of Accounts rows:     141
Mapped transactions:      2,486
Unmatched transactions:      15
Mapping rate:              99.40%
```

Prototype P&L comparison baseline:

```text
Income:                $1,727,929.90
Cost of Goods Sold:     -$394,532.12
Gross Profit:          $1,333,397.78
Expenses:               -$956,767.16
Net Income:              $376,630.62
```

These values are regression references, not finalized or approved financial
statements.

---

## Roadmap

1. Baseline and project foundation
2. Deterministic transaction domain
3. Deterministic rule engine
4. Chart of Accounts validation
5. Trial Balance and Profit and Loss stabilization
6. PostgreSQL and repository foundation
7. FastAPI backend
8. Streamlit API integration
9. Processing runs and background workflows
10. Exception review and rule management
11. Authentication, authorization, and audit
12. Azure staging and deployment
13. Embeddings and AI recommendations
14. AI Financial Assistant

---

## Product Status

BSI is currently under active development.

The application should be treated as a draft financial automation system until
its deterministic calculations, security controls, workflow controls, and
financial golden tests have been reviewed and approved.