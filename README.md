# AI-Powered HR Assistant Platform

An intelligent, role-aware Human Resources Assistant powered by Large Language Models (LLMs), LangChain, and SQLite. The platform automates everyday HR operations through natural language interactions while enforcing role-based access control, maintaining audit trails, and protecting sensitive employee information.

From leave management and reimbursement claims to onboarding workflows and anonymous employee feedback, the system brings multiple HR operations into a unified conversational interface.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![LangChain](https://img.shields.io/badge/Framework-LangChain-orange)
![Database](https://img.shields.io/badge/Database-SQLite-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Overview

Traditional HR workflows often involve repetitive queries, manual approvals, fragmented records, and time-consuming administrative processes.

This project explores how an LLM-powered agent can simplify these operations by connecting natural language understanding with structured backend tools.

Unlike a basic chatbot, the assistant can invoke specific HR operations, validate requests, enforce permissions, and interact with a relational database. Sensitive messages are intercepted by a dedicated escalation mechanism before reaching the language model.

**Key capabilities:**

- Conversational leave management and entitlement tracking.
- Reimbursement submission with category-based validation.
- Employee ticket management and onboarding workflows.
- Anonymous employee pulse surveys.
- Role-based authorization for employees, managers, and HR.
- Automatic detection and escalation of sensitive messages.
- Database-backed operations with audit logging.

---

## System Architecture

The application follows a tool-driven agent architecture in which the LLM interprets employee requests and selects appropriate HR tools.

```text
                   +---------------------------+
                   |     Employee / Manager    |
                   |       HR Interaction      |
                   +-------------+-------------+
                                 |
                                 v
                   +---------------------------+
                   |   Keyword Guard & Regex   |
                   |   Sensitive Detection     |
                   +-------------+-------------+
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
          +------------------+      +-------------------+
          | Escalation       |      | LangChain Agent   |
          | Handler          |      | (LLM + Tools)     |
          +--------+---------+      +---------+---------+
                   |                          |
                   v                          v
          +------------------+      +-------------------+
          | Escalations      |      | HR Tool Selection |
          | Database Table   |      +---------+---------+
          +------------------+                |
                                              v
                              +---------------+---------------+
                              |                               |
                     +--------+-------+              +--------+--------+
                     | Leave &        |              | Reimbursement   |
                     | Entitlements   |              | Claims          |
                     +----------------+              +-----------------+
                              |
                     +--------+--------+
                     | Onboarding &    |
                     | Ticketing       |
                     +-----------------+
                              |
                     +--------+--------+
                     | Pulse Surveys   |
                     | & HR Operations|
                     +-----------------+
                              |
                              v
                     +-----------------+
                     | SQLite Database |
                     | Audit & Records |
                     +-----------------+
```

### Request Processing Flow

1. **User Input:** An employee or authorized HR user submits a request in natural language.
2. **Sensitive Message Detection:** A keyword and regex-based guard checks for predefined high-risk scenarios.
3. **Escalation:** If a sensitive message is detected, the system creates an escalation record without passing the message to the LLM.
4. **Intent Understanding:** Otherwise, the LangChain agent interprets the request and identifies the appropriate tool.
5. **Authorization:** Tool-level access restrictions enforce the user's role and organizational relationships.
6. **Execution:** The selected tool validates the request and performs the corresponding database operation.
7. **Response:** The assistant returns the result in a conversational format.

---

## Core Features

### 1. Intelligent Leave Management

Manage employee leave requests through natural language interactions.

- Check available leave balances and entitlements.
- Submit leave applications.
- Track pending and approved requests.
- Validate leave-related operations.
- Understand direct manager-employee relationships.

### 2. Reimbursement Management

Simplify employee expense and reimbursement workflows.

- Submit reimbursement claims.
- Validate claim categories.
- Apply category-specific boundaries.
- Track reimbursement requests through tickets.

### 3. Employee Ticketing & Onboarding

Automate routine employee lifecycle operations.

- Create and query HR tickets.
- View onboarding and offboarding checklists.
- Track lifecycle task completion.
- Access team-level task information based on permissions.
- Draft HR documents such as welcome emails and experience letters.

### 4. Anonymous Employee Pulse Surveys

Collect employee feedback while separating survey responses from identifiable employee records.

- Submit employee pulse survey responses.
- Generate salted SHA-256 participant hashes.
- Store responses separately from employee identifiers.
- Support aggregate feedback reporting.
- Exclude pulse submissions from the regular audit-log tool list.

### 5. Role-Based Access Control (RBAC)

The assistant uses employee identity and assigned roles to restrict access to sensitive HR operations.

| Role | Permissions |
|---|---|
| Employee | View personal leave balances, tickets, and lifecycle tasks; submit permitted requests |
| Manager | View team reports and manage requests from direct reports |
| HR | Execute offboarding workflows, draft formal letters, and access aggregated survey data |
| Admin | Administrative access as configured by the application |

Authorization is enforced at the tool level rather than relying solely on the LLM's interpretation of user permissions.

### 6. Automatic Sensitive Message Escalation

A dedicated keyword and regex-based guard identifies predefined sensitive scenarios, including:

- Harassment complaints.
- Legal action references.
- Other configured high-risk HR concerns.

When a match is detected:

- The message bypasses the LLM.
- An escalation record is created in the database.
- The escalation is assigned a default pending status.
- The request follows the designated HR escalation workflow.

---

## Database Design

The application uses SQLite as its relational database, with `hr_assistant.db` as the default database file.

### Database Schema

| Table | Responsibility |
|---|---|
| `employees` | Employee directory, roles, employment status, and manager relationships |
| `leave_balances` | Employee leave allocations and usage |
| `tickets` | HR requests and their processing states |
| `lifecycle_tasks` | Employee onboarding and offboarding checklists |
| `pulse_participants` | Hashed survey participation tracking |
| `pulse_responses` | Anonymous employee survey responses |
| `escalations` | Automatically flagged sensitive HR scenarios |
| `audit_log` | Records of tool invocations and operational activities |

### Important Database Design Decisions

- Unique employee identifiers maintain employee record consistency.
- Employee-specific leave balances support separate leave categories.
- Composite primary keys are used for lifecycle task records.
- Foreign-key relationships maintain referential integrity where configured.
- Pulse survey participation and responses are separated from the employee directory.
- Escalation records maintain a default pending state for follow-up.

---

## Security & Privacy

Security is an important part of the platform because HR systems process sensitive employee information.

### Role-Based Authorization

Access to tools is restricted according to the authenticated employee's role and reporting relationships.

A manager's permissions are scoped to their direct reports rather than all employees.

### Sensitive Request Handling

Predefined sensitive messages are intercepted before LLM processing, reducing the possibility of the language model handling designated high-risk requests.

### Audit Logging

Tool invocations are recorded in the `audit_log` table to provide operational traceability.

Pulse survey submission tools are excluded from the regular audit-log tool list to reduce direct linkage between survey activity and employee identity.

### Survey Anonymity

The application uses a salted SHA-256 hash derived from the configured salt and employee identifier to track survey participation.

The survey responses are stored separately from direct employee identifiers.

**Privacy consideration:** Hashing an employee identifier does not provide cryptographic anonymity against someone who knows the identifier and salt. True anonymity also depends on access controls, database separation, retention policies, and the absence of identifying metadata.

---

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application logic and backend implementation |
| LangChain | LLM agent orchestration and tool integration |
| Transformers | Model and tokenizer integration |
| PyTorch | Model inference support |
| SQLite | Relational data persistence |
| Regular Expressions | Sensitive message detection |
| SHA-256 | Salted participant hash generation |

---

## Getting Started

### Prerequisites

- Python 3.10 or higher.
- A compatible language model and tokenizer.
- Required model dependencies.
- Access to the HR application source files.

### 1. Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_REPOSITORY_NAME>
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate it:

**Windows**
```bash
venv\Scripts\activate
```

**Linux / macOS**
```bash
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install langchain-core transformers torch
```

SQLite is included with standard Python installations through the `sqlite3` module, so it does not need to be installed separately using pip.

### 4. Initialize the HR Assistant

The application requires a compatible tokenizer and model pipeline.

```python
import hr_tools
from datetime import date

# Initialize the application
hr_tools.init(
    tokenizer,
    pipeline_object,
    db_path="hr_assistant.db"
)

# Configure survey hashing
hr_tools.PULSE_SALT = "YOUR_SECURE_SALT_KEY"

# Configure application date
hr_tools.TODAY = date.today()
```

Replace `tokenizer` and `pipeline_object` with the actual model and tokenizer instances used by your implementation.

**Important:** Store the survey salt and other sensitive configuration values in environment variables rather than hardcoding them in production.

---

## Usage Examples

Once initialized, the assistant can be accessed through `run_agent()`.

### Employee: Check Leave Balance

```python
response = hr_tools.run_agent(
    "How many casual leaves do I have left?",
    uid="E101"
)

print(response)
```

### Employee: Submit a Request

```python
response = hr_tools.run_agent(
    "I want to apply for leave next Monday.",
    uid="E101"
)

print(response)
```

### Manager: View Team Requests

```python
response = hr_tools.run_agent(
    "Show pending requests from my team.",
    uid="E200"
)

print(response)
```

### HR: Access Employee Lifecycle Operations

```python
response = hr_tools.run_agent(
    "Show pending offboarding tasks.",
    uid="E300"
)

print(response)
```

The examples assume that the corresponding employee records, roles, and permissions have been configured in the database.

---

## Configuration

The following application-level settings are used during initialization:

| Setting | Description |
|---|---|
| `tokenizer` | Tokenizer associated with the selected language model |
| `pipeline_object` | Configured model inference pipeline |
| `db_path` | SQLite database file location |
| `PULSE_SALT` | Secret salt used for participant hash generation |
| `TODAY` | Application date used for date-dependent operations |

---

## Design Highlights

This project focuses on combining LLM flexibility with conventional software engineering controls.

**Tool-driven execution:** The LLM does not directly manipulate the database. It interacts through predefined tools.

**Deterministic validation:** Business rules, such as reimbursement boundaries and leave validations, are handled by application logic.

**Permission-aware execution:** Access decisions are enforced through role and reporting-relationship checks.

**Pre-LLM escalation:** Sensitive scenarios can be intercepted without depending on the model's interpretation.

**Structured persistence:** SQLite provides a consistent storage layer for employee records, requests, lifecycle tasks, and operational logs.

---

## Future Enhancements

Potential directions for extending the platform include:

- Web-based HR dashboard for employees, managers, and HR administrators.
- Integration with enterprise authentication providers.
- Email and notification integration for approval workflows.
- Advanced analytics for employee pulse surveys.
- More sophisticated sensitive-message classification.
- Comprehensive automated testing for authorization and workflow boundaries.
- Migration to a production-grade relational database for larger deployments.

---

## Limitations & Responsible Use

- The assistant is intended to support HR workflows, not replace human HR decision-making.
- Sensitive escalations require appropriate human review and follow-up.
- Regex-based detection depends on predefined patterns and may miss context-dependent concerns.
- LLM-generated HR documents should be reviewed before official use.
- Survey anonymity depends on the complete data-handling architecture, not hashing alone.
- Production deployment requires appropriate authentication, authorization, encryption, monitoring, and data-retention policies.

---

## License

This project is licensed under the [MIT License](https://opensource.org/licenses/MIT).

---

**Built to explore the intersection of AI agents, enterprise automation, structured tool execution, and secure HR workflows.**
