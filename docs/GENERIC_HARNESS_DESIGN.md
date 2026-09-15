# From Denial-Triage Tool to Generic Structured-Response Harness

## 1. What this is

The Denial Triage & Appeal system is already three things stacked together,
and only one of them is actually about insurance denials:

1. A **generic document-intake → classify → generate pipeline** with
   forced structured extraction, confidence-gated classification, grounded
   generation, an eval/regression harness, and a full audit trail.
2. A **config-per-tenant abstraction** (`CompanyProfile`) that swaps the
   extraction schema, classification taxonomy, and generation prompts
   without touching pipeline code.
3. A **specific vertical** — denial triage + appeal drafting — implemented
   as one `CompanyProfile` pair (Comprehensive EyeCare Partners, Reliable
   Medical DME).

The generalization is: keep (1) and (2) as the product. Turn (3) into the
first of many "use-case profiles." Add a **connector layer** on both ends —
inbound (how a document/email enters the pipeline) and outbound (how a
structured response leaves it and reaches the platform it needs to land
in) — so the same core harness can run against email inboxes across
multiple platforms, not just an upload form.

```
                    ┌──────────────────────────────────────────┐
                    │              CORE HARNESS                 │
                    │  (unchanged pipeline: extract → classify   │
                    │   → generate, eval harness, audit trail)   │
                    └──────────────────────────────────────────┘
                             ▲                        │
                  normalized │                        │ structured
                  IntakeItem │                        │ response object
                             │                        ▼
        ┌────────────────────────┐        ┌────────────────────────┐
        │   INBOUND CONNECTORS     │        │   OUTBOUND CONNECTORS    │
        │  Gmail / M365 / Front /  │        │  reply draft, new email, │
        │  IMAP poll / webhook     │        │  ticket update, Slack…   │
        └────────────────────────┘        └────────────────────────┘
                             ▲
                             │
                    ┌──────────────────┐
                    │  USE-CASE PROFILE │  ← this is the old CompanyProfile,
                    │  (per use case,    │    renamed and widened: denial
                    │   per tenant)      │    triage is profile #1, not
                    └──────────────────┘    special-cased in code
```

## 2. What changes in the data model / abstractions

- **`CompanyProfile` → `UseCaseProfile`.** Same shape (extraction tool
  schema, system prompts per stage, classification taxonomy, generation
  guidance), but the taxonomy is no longer hardcoded to the six denial
  root-causes — each `UseCaseProfile` declares its own category set. The
  six-category denial taxonomy becomes one profile's config, not a
  platform-level constant. Denial triage keeps its "should not invent new
  categories" discipline *within its own profile*; other profiles define
  their own.

- **`denials` table → `intake_items`.** Rename and widen: same columns
  conceptually (source, raw content, extracted fields, classification,
  confidence, generated response, status, audit trail), plus new columns
  for connector provenance (`source_connector`, `source_platform_id`,
  `thread_id`, `received_at`). The existing multi-tenant + audit-trail
  design (Postgres, `source_company`-keyed profile lookup) carries over
  directly — swap the lookup key from company to `(tenant_id,
  use_case_profile_key)`.

- **New `IntakeItem` normalization contract.** Every connector's job is to
  turn a platform-native message (Gmail message object, Graph API email,
  Front conversation) into one normalized shape before it touches the
  pipeline:
  ```python
  @dataclass
  class IntakeItem:
      source_connector: str        # "gmail", "m365", "front", "imap"
      source_platform_id: str      # platform's native message/thread id
      thread_id: str | None
      received_at: datetime
      from_address: str
      subject: str
      body_text: str
      body_html: str | None
      attachments: list[Attachment]  # filename, content_type, bytes/url
      raw_headers: dict            # kept for audit trail, not parsed
  ```
  This is the seam that makes the pipeline platform-agnostic: `extract.py`
  and `classify.py` already only care about structured fields + text, not
  where it came from.

- **New routing step before classification: "does this even need a
  structured response?"** Not every inbound email is a case. Add a cheap
  triage classifier (or rule-based prefilter — sender domain, subject
  pattern, an explicit "intake address" like `appeals@client.com`) that
  decides whether an item enters the pipeline at all, before spending a
  full extraction+classification call on it. This is new — the current
  system assumes every uploaded document is a denial.

- **Outbound side: `ResponseDelivery` abstraction.** Mirrors the intake
  side. Given a generated structured response (the appeal draft, or
  whatever a given use-case profile generates), a connector-specific
  adapter decides how it lands: Gmail/M365 draft-reply in the original
  thread, a Front conversation reply, a webhook POST to a downstream
  system, or (always available as a fallback) a dashboard queue item a
  human copies out manually. Human-in-the-loop stays the default:
  generated responses land as **drafts**, not auto-sent, unless a tenant
  explicitly opts a use-case profile into auto-send with its own
  confidence threshold — same confidence-gating principle already in the
  eval harness, just applied to a "may I act automatically" decision.

## 3. Connector layer design

Two responsibilities per connector, symmetric in shape:

- **Inbound**: authenticate, poll or receive webhook, fetch new
  messages, normalize to `IntakeItem`, hand off to the triage/pipeline
  queue. Must support incremental sync (don't reprocess old mail) and
  dedup (idempotency on `source_platform_id`).
- **Outbound**: authenticate, take a generated response + target
  thread/conversation reference, create a draft or reply, record the
  platform's confirmation id back onto the `intake_items` row for audit.

Both sides share one auth/credential-store abstraction (OAuth2 token
refresh, API key rotation) keyed per tenant per connector, so adding a new
platform means implementing one adapter class against a fixed interface —
not touching the core harness.

```python
class InboundConnector(Protocol):
    def fetch_new(self, since: datetime) -> list[IntakeItem]: ...
    def mark_processed(self, source_platform_id: str) -> None: ...

class OutboundConnector(Protocol):
    def deliver_draft(self, item: IntakeItem, response: GeneratedResponse) -> DeliveryReceipt: ...
    def deliver_sent(self, item: IntakeItem, response: GeneratedResponse) -> DeliveryReceipt: ...
```

## 4. Platform feasibility — what's a real connector target and what isn't

### Solid, build these first

| Platform | Inbound | Outbound | Auth | Notes |
|---|---|---|---|---|
| **Google Workspace (Gmail API)** | Yes — `messages.list`/`history.list` with push notifications via Pub/Sub, or polling | Yes — `drafts.create`, `messages.send` on a thread | OAuth2, domain-wide delegation for org-wide install | Best-documented, push notifications make near-real-time triage cheap. Requires Google Workspace admin consent for org-wide use (consumer Gmail is far more restricted and not a realistic B2B target). |
| **Microsoft 365 / Outlook (Graph API)** | Yes — `/messages` list + delta query, or webhook subscriptions | Yes — `/messages/{id}/createReply` (draft) or `/sendMail` | OAuth2 (app registration), admin consent for org-wide | Equally solid, the enterprise-standard counterpart to Gmail. Delta query gives efficient incremental sync. This is the higher-value target for regulated/enterprise clients like Ortho Med-style shops running Microsoft shops. |
| **Front** | Yes — REST API + real-time webhooks (`inbound_message`, `conversation_updated`) | Yes — draft/reply via API into the same conversation | API token or OAuth app | Front is *built* for this pattern (shared inbox + structured workflows) — arguably the easiest integration since it's designed around exactly this "route conversation → structured action" model. |
| **Generic IMAP/SMTP** | Yes — IMAP IDLE or polling for any mail server that exposes IMAP | Yes — SMTP send, though "reply as a draft in the original client" isn't a universal IMAP concept (drafts folder exists but isn't a first-class API like Gmail/Graph) | Username/password or app-specific password, no OAuth in the base protocol | The fallback connector — covers anything without a dedicated API (smaller providers, self-hosted mail). Weaker UX (no native "draft reply in thread" semantics) but universal reach. |
| **Zendesk / Help Scout / Intercom (support-desk platforms)** | Yes — all expose REST APIs + webhooks for new tickets/conversations | Yes — reply/comment APIs | API token or OAuth | Same pattern as Front, one tier down in ubiquity. Worth building if a target client already lives in one of these rather than raw email. |
| **Inbound email parsing services (Postmark Inbound, SendGrid Inbound Parse, Mailgun Routes)** | Yes — these turn "email sent to a specific address" into a webhook payload directly | N/A (send-only, use SMTP/API to reply) | API key | Useful as a *simpler* inbound-only path when you don't need to read an existing mailbox — you just give the client a dedicated intake address (`appeals@client-domain.com` forwarded via MX or alias) and every email to it becomes a webhook. Lower integration lift than full Gmail/Graph OAuth for a client that just wants "emails to this address get triaged." |

### Possible but meaningfully harder — build later if a client needs it

| Platform | Why harder |
|---|---|
| **On-prem Exchange (not Microsoft 365)** | No Graph API — you're back to EWS (Exchange Web Services, being deprecated) or raw IMAP/MAPI against an on-prem server, plus whatever VPN/network access the client's IT will allow. Real enterprise demand exists (regulated industries running on-prem) but each install is bespoke. |
| **Shared/generic mailboxes behind strict corporate DLP or CASB (e.g. some Salesforce/ServiceNow email-to-case setups)** | Technically has APIs, but access is usually gated behind the platform's own workflow engine rather than a clean "read new mail" primitive — integration effort is closer to "build a Salesforce app" than "call an email API." |
| **Consumer Gmail / Outlook.com (personal accounts, not Workspace/M365 org accounts)** | APIs exist but Google/Microsoft apply much stricter app-review and sensitive-scope requirements for consumer accounts (verification, limited-use disclosures) — realistic for a demo, not a fast B2B sales motion. Always target the *business* tier of each platform. |

### Not realistically possible as a direct connector

| Platform / scenario | Why not |
|---|---|
| **Platforms with no API and no webhook/forwarding mechanism at all** | If a mail system genuinely has zero programmatic access and the client's IT won't set up forwarding to an intake address, there's no integration path — this is a "human still does it" case, not a connector gap to close later. |
| **End-to-end encrypted mail (e.g. ProtonMail without their bridge, encrypted PGP threads)** | The whole point of E2E encryption is that nothing but the recipient's client can read the plaintext — a server-side connector structurally cannot read the content to classify it. Only viable if the client runs a local bridge/desktop agent that decrypts client-side, which is a different (heavier) architecture than everything else here. |
| **Reading a mailbox without any consent/authorization from its owner** | Not a technical limitation — a hard no regardless of API existence. Every connector in this design requires the tenant's own OAuth consent or API credentials; there's no "just read someone's inbox" mode, by design and for good reason. |

## 5. Build order (incremental, doesn't block current work)

1. **Extract the abstraction, no behavior change.** Rename
   `CompanyProfile` → `UseCaseProfile`, `denials` → `intake_items`
   (or add the new columns without breaking the old ones yet). Denial
   triage keeps working exactly as-is, now as "the first use-case
   profile" instead of the whole product.
2. **Build the `IntakeItem` / connector Protocol interfaces**, then
   implement exactly one connector end-to-end (Gmail is the best first
   target — best docs, push notifications, and it's the platform you
   already have a Google account on for testing) plus the existing
   upload-form path becomes a trivial "manual connector" that already
   works today.
3. **Add the pre-classification triage/routing step** so not every
   inbound email is assumed to be a case.
4. **Build the outbound draft-delivery adapter** for the same platform,
   landing generated responses as drafts in the original thread.
5. **Add a second connector (Microsoft Graph or Front)** to prove the
   abstraction actually holds — if adding platform #2 requires touching
   the core pipeline, the abstraction boundary was wrong and needs
   revisiting before adding more.
6. **Second use-case profile**, something outside healthcare entirely
   (e.g. a support-ticket triage or vendor-dispute-response profile), to
   prove the taxonomy/prompt abstraction generalizes as well as the
   connector layer does.
7. **Tool-augmented generation (section 7)** — only build this once a real
   profile actually needs it. Denial triage doesn't need it to work today
   (the letter carries its own facts); it becomes real the moment someone
   wants evidence-grounded appeals or a diagnostic-lookup profile. Don't
   build the MCP-binding plumbing speculatively — build it against the
   first profile that has an actual external data source to call.

## 6. What stays exactly as it is today

- The eval/regression harness pattern (fixed test set, graded scoring,
  run on every profile/prompt change) — this doesn't change shape at all,
  it just gets pointed at more profiles over time.
- The audit trail design (every AI + human decision logged, immutable) —
  connectors add rows to the same trail, they don't need their own
  parallel logging system.
- The confidence-gated human review pattern — becomes the same gate that
  decides draft-vs-send on the outbound side, not a new concept.

## 7. Tool-augmented generation: MCP access per use-case profile

Everything in sections 1-6 assumes generation has everything it needs
already sitting in `extracted` + `classification`. That's true for denial
triage today (the letter itself carries every fact the appeal draft cites),
but it isn't true in general. Two motivating cases:

- **Denial triage, done properly**: a strong appeal doesn't just restate
  the claim, it cites supporting evidence — prior authorization on file,
  the patient's actual clinical record, prior claims history showing a
  pattern. That evidence usually lives in a system the harness doesn't
  have inline access to: a claims/EHR-adjacent database, reachable only
  through the client's own secured interface.
- **A hypothetical bug-triage profile**: routing an inbound bug report
  isn't just classification — a good response cites what's actually
  wrong, which means querying a root-cause-analysis tool (log
  correlation, stack-trace clustering, whatever the org already runs)
  mid-draft, not guessing from the bug report text alone.

Both are the same shape: **generation sometimes needs to call out to an
external, tenant-specific data source before it can write a grounded
response.** That's a real architecture change, not a config tweak — it
means the generation stage stops being a single forced-tool call and
becomes an actual agentic loop (see the harness/tool-loop pattern already
documented for module 1 of the AI Engineer Academy content): the model can
emit a tool call, the harness executes it against an MCP server, the
result comes back into context, and the model continues — potentially
several rounds — before producing the final draft.

### 7.1 What a profile declares vs. what a tenant configures

Same separation of concerns as the rest of the profile abstraction: a
`UseCaseProfile` declares *which* MCP capabilities its generation stage is
allowed to use; the actual connection, credentials, and endpoint are
tenant-specific runtime config, resolved at call time — never baked into
the profile code, which is shared across every tenant running that profile.

```python
@dataclass(frozen=True)
class MCPServerBinding:
    name: str                  # e.g. "medical_records", "root_cause_debugger"
    allowed_tools: list[str]   # explicit allowlist -- not "every tool this server exposes"
    required: bool             # True: missing/unreachable blocks/downgrades to human review.
                                # False: generation degrades gracefully (skip augmentation,
                                # flag lower confidence) rather than failing outright.
    data_sensitivity: str      # "phi" | "internal" | "public" -- drives audit-log
                                # handling (see 7.3) and whether results can be cached.

# Declared on the profile (shared code, no tenant-specific values):
mcp_servers: list[MCPServerBinding] = [
    MCPServerBinding(
        name="medical_records",
        allowed_tools=["lookup_prior_authorization", "lookup_claims_history"],
        required=True,          # an appeal drafted without checkable evidence
                                 # should not go out as a confident auto-draft
        data_sensitivity="phi",
    ),
]

# Resolved per-tenant at runtime, outside the profile, from a credential
# store keyed on (tenant_id, mcp_server_name) -- endpoint URL, auth
# (API key / OAuth / mTLS cert), and which of the tenant's own systems it
# actually points at. The profile never sees another tenant's binding.
```

### 7.2 Tenant isolation is the actual hard requirement here

This is the one place in the whole design where a mistake is genuinely
dangerous, not just embarrassing — cross-tenant data leakage through a
shared MCP binding (Company A's generation call somehow reaching Company
B's medical-records server, or vice versa) would be a real compliance
failure, not a bug report. Two independent layers, deliberately redundant:

1. **Harness-level**: MCP server connection + credentials are always
   resolved from `(tenant_id, mcp_server_name)`, sourced from the same
   request context that already scopes everything else (the `intake_item`
   being processed). There is no code path where a generation call can
   reach a binding belonging to a different tenant than the one it's
   processing for.
2. **Tool-level, at the MCP server itself**: the tools a sensitive server
   exposes should be scoped narrow by design — "look up the prior
   authorization for claim ID X" (X already known from the extraction
   stage), not "search all patient records." Least-privilege at the tool's
   own capability surface, not just at the harness's allowlist — the
   allowlist is defense in depth, not the only control.

`allowed_tools` matters independently of tenant isolation too: an MCP
server may expose more tools than any single profile should be able to
call (e.g. a medical-records server might also expose write/update tools
that no drafting profile should ever touch) — the allowlist is what keeps
a profile that only needs read-only lookups from ever being handed a tool
that can mutate the underlying system.

### 7.3 Audit trail for tool calls, and what a PHI-sensitivity tag actually does

Every MCP tool call and its result gets a row in the same audit trail as
everything else — this isn't a new logging system, it's the existing
"every AI and human decision logged, immutable" principle extended to
cover tool calls the model made during generation. What `data_sensitivity`
controls is *what gets persisted*, not *whether* it gets logged:

- `"public"` / `"internal"`: log the full tool call + result verbatim,
  same as any other audit row.
- `"phi"`: log that the call happened, which tool, which record was
  referenced (e.g. a claim/auth ID, not the clinical content itself), and
  a hash/reference rather than the raw sensitive payload. The raw result
  stays in the model's context for that single generation call and is not
  persisted a second time in a general-purpose audit table. If deeper
  forensic replay is ever needed, that's a separate, more tightly
  access-controlled store — not the same table everything else lands in.

### 7.4 Confidence gating extends naturally to "did the lookup even work"

This isn't a new concept layered on top of the existing confidence-gate
pattern — a failed or ambiguous MCP lookup is just another input to the
same gate that already decides `needs_review`. Concretely:

- `required=True` binding unreachable/times out → generation does not
  proceed to a confident auto-draft; the item routes to human review with
  a clear note of what couldn't be verified. (This is the right default
  for the appeal-evidence case — a confident-sounding appeal that turns
  out to cite nothing real is worse than no draft.)
- `required=False` binding unreachable → generation proceeds without that
  augmentation, but the resulting confidence score reflects the gap (e.g.
  a bug-triage draft that couldn't get a root-cause hit should say so and
  score lower than one that did, not silently guess and sound equally
  sure either way).
- Tool call succeeds but returns something ambiguous/conflicting (e.g. two
  contradictory prior-authorization records) → treated the same as any
  other low-confidence classification: flagged for human review rather
  than the model picking one and moving on.
