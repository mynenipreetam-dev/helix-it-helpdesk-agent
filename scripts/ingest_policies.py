"""
Policy ingestion script.

Chunks POL-01..POL-10 by section (e.g. POL-01 §1.1) and upserts into ChromaDB.

Run once before starting the agent:
    poetry run python scripts/ingest_policies.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chromadb
from chromadb.utils import embedding_functions

from agent.config import settings

# ── Full policy text ──────────────────────────────────────────────────────

POLICIES = """
POL-01 — Password & Authentication Policy
1.1 Standard user passwords must be at least 14 characters, contain three of four character classes, and not match any of the previous 12 passwords.
1.2 Standard accounts rotate passwords annually. Privileged accounts (domain admins, root, DBA) rotate every 90 days.
1.3 Multi-factor authentication (MFA) is mandatory for every corporate application and is enforced via Okta. Acceptable second factors: Okta Verify push, FIDO2 security key, or TOTP.
1.4 Accounts are locked after 5 consecutive failed login attempts. Self-service unlock is available after 15 minutes via the password portal; otherwise contact the Service Desk.
1.5 1Password Enterprise is the sanctioned password manager. Storing corporate credentials in browsers, sticky notes, or personal password managers is prohibited.
1.6 Privileged users must additionally authenticate with a YubiKey 5 series hardware token. Soft tokens alone do not satisfy privileged access requirements.

POL-02 — VPN & Remote Access Policy
2.1 Cisco AnyConnect is the only approved VPN client. Personal VPNs (NordVPN, ExpressVPN, etc.) must not be installed on corporate endpoints.
2.2 Split tunneling is disabled by policy. All traffic is routed through the corporate gateway and inspected by Zscaler.
2.3 VPN sessions terminate after 12 hours of connection or 30 minutes of inactivity, whichever comes first.
2.4 Public or untrusted Wi-Fi (hotels, cafes, airports) is permitted only when AnyConnect is active before any other traffic.
2.5 Access is geo-restricted to the Approved Country List. Connecting from outside the list requires a Travel Exception ticket submitted at least 5 business days in advance.
2.6 Privileged remote access to production systems is brokered through CyberArk PAM. Direct SSH/RDP to production from a laptop is forbidden.

POL-03 — Acceptable Use Policy
3.1 Corporate devices are issued primarily for business use. Incidental personal use is allowed when it does not interfere with work or violate any other policy.
3.2 Prohibited activities: peer-to-peer file sharing, gambling, adult content, cryptocurrency mining, and any unlicensed streaming.
3.3 Web traffic is filtered and logged via Zscaler. Logs are retained for 12 months and reviewed only on lawful basis.
3.4 USB mass storage is blocked by default. Exceptions for business need can be requested via the USB Exception form in ServiceNow with manager approval.
3.5 Personal cloud storage (Dropbox personal, iCloud Drive, Google Drive consumer) is blocked. Use the corporate Box tenant or OneDrive for Business instead.
3.6 Corporate devices must remain under the control of the assigned employee. Lending the device to family members or external parties is prohibited.

POL-04 — Software Installation & Procurement Policy
4.1 Only software listed in the Approved Software Catalog (ServiceNow > Software Center) may be installed without a ticket. End users can self-serve catalog apps.
4.2 New software requests follow a 5-business-day SLA. Reviews include InfoSec (data classification, telemetry), Procurement (licensing), and Legal (terms of service).
4.3 Open-source libraries used in internal tools require a Software Bill of Materials (SBOM) and a license review. GPL and AGPL components require special exception.
4.4 Corporate email addresses must not be used to sign up for unapproved SaaS free trials or personal accounts.
4.5 Browser extensions are restricted to the Allowed Extensions List enforced via Chrome and Edge management. Other extensions are blocked at install time.
4.6 Local admin rights are removed by default. Time-bound admin elevation can be requested through Make-Me-Admin for a maximum of 60 minutes per session.

POL-05 — Data Classification & Handling Policy
5.1 Helix data is classified into four tiers: Public, Internal, Confidential, and Restricted. Every document inherits the highest tier of any field it contains.
5.2 Restricted data (PHI, payment card data, source code for revenue-critical systems) must be encrypted at rest and in transit, and may only reside in approved geographies (US-East, EU-Central).
5.3 Confidential data may not be sent to external recipients without a Data Loss Prevention (DLP) exception. Requires data owner approval, valid for 30 days.
5.4 EU personal data is subject to GDPR controls; transfer outside the EEA requires Standard Contractual Clauses on file.
5.5 Retention follows the published Records Retention Schedule. Default: 7 years; PHI: 10 years; payment data purged at 13 months.
5.6 Auto-forwarding corporate email to any external address (including personal Gmail) is technically blocked and policy-prohibited.

POL-06 — BYOD Policy
6.1 Personal devices are permitted for corporate email, calendar, and Teams only. They must be enrolled in Microsoft Intune for MDM management.
6.2 Enrollment establishes a managed work container. IT can remote-wipe only the corporate container; personal data is not touched.
6.3 Restricted and Confidential data must never be stored on a BYOD device outside the managed container.
6.4 Jailbroken or rooted devices are blocked from enrollment and from accessing corporate resources.
6.5 Operating systems must be within two major versions of current vendor releases. Older OS versions lose access automatically.
6.6 A $50/month BYOD stipend is available for roles flagged mobile-eligible in Workday. Employees on the stipend forfeit eligibility for a corporate-issued mobile phone.

POL-07 — Email & Communication Security Policy
7.1 All outbound email is sent over TLS. Recipients whose servers do not support TLS receive a portal-pickup link instead of plaintext mail.
7.2 Suspicious emails should be reported using the Phish Alert Button in Outlook. Do not forward suspicious emails manually.
7.3 Every email from an external sender is prefixed with an [EXTERNAL] banner. Treat any CEO request from an [EXTERNAL] address as suspect.
7.4 Attachments larger than 25 MB are blocked at the gateway. Use Box or OneDrive sharing links for large files.
7.5 Phishing simulations run monthly. Employees who fail two simulations within 12 months are auto-enrolled in additional training.
7.6 Auto-forwarding rules to external addresses are blocked at the mailbox level and cannot be created by end users.

POL-08 — Hardware Request & Asset Management Policy
8.1 The standard laptop refresh cycle is 36 months, calculated from the asset first-issue date in the CMDB.
8.2 New-hire hardware requests must be submitted by the hiring manager at least 10 business days before the start date.
8.3 Lost or stolen devices must be reported within 24 hours via the Lost/Stolen Device ticket. A police report case number must be attached if stolen.
8.4 Repairs are performed only at the IT Depot in Austin or by approved third-party vendors (Apple Business Repair, Dell ProSupport). Walk-in repairs are not reimbursable.
8.5 On offboarding, IT mails a prepaid return kit. Devices must be shipped within 5 business days of the last working day.
8.6 Peripherals (monitor, keyboard, mouse, dock) follow a 5-year refresh and are requested through the Peripheral Catalog.

POL-09 — Security Incident Reporting Policy
9.1 Suspected security incidents must be reported within 1 hour of discovery to security@helix.example or via the 24/7 SOC hotline at extension 4357.
9.2 If you suspect a compromise, do NOT power off the device. Disconnect from the network and wait for SOC instructions to preserve forensic evidence.
9.3 Severity tiers: SEV-1 (active breach, customer data at risk), SEV-2 (probable breach or major outage), SEV-3 (contained incident), SEV-4 (informational).
9.4 An Incident Commander is assigned for SEV-1 and SEV-2. The IC owns external communication; employees must not speak to press about the incident.
9.5 Tabletop exercises are mandatory quarterly for employees tagged incident-responder in Workday.
9.6 Lost or stolen devices confirmed to contain Restricted data are automatically escalated to a SEV-2 incident.

POL-10 — Access Provisioning & Deprovisioning Policy
10.1 New hires receive access based on the RBAC template attached to their Workday job code when HR marks them active.
10.2 Any access beyond the default RBAC template requires manager approval plus, for Restricted-tier systems, data owner approval.
10.3 Access reviews are run quarterly. Reviewers have 14 calendar days to certify or revoke; un-certified access is automatically revoked.
10.4 On termination or resignation, all access is revoked within 1 hour of HR triggering the separation event in Workday.
10.5 Contractor accounts have a maximum 6-month duration and require manager renewal. Unrenewed accounts disable automatically.
10.6 Shared accounts are prohibited. Exceptions are limited to service accounts owned by a named engineering team with rotating credentials in CyberArk.
"""


def chunk(text: str) -> list[dict]:
    chunks = []
    current_policy = None
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Policy header: "POL-XX — ..."
        m = re.match(r"^(POL-\d+)\s+—\s+.+$", line)
        if m:
            current_policy = m.group(1)
            continue
        # Section: "X.Y text..."
        m = re.match(r"^(\d+\.\d+)\s+(.+)$", line)
        if m and current_policy:
            section, content = m.group(1), m.group(2)
            cid = f"{current_policy}-{section}"
            chunks.append({
                "id":       cid,
                "document": f"{current_policy} §{section}: {content}",
                "metadata": {
                    "policy_id": current_policy,
                    "section":   f"§{section}",
                    "chunk_id":  cid,
                },
            })
    return chunks


def main():
    chunks = chunk(POLICIES)
    print(f"Parsed {len(chunks)} policy sections")

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    ef = embedding_functions.DefaultEmbeddingFunction()
    col = client.get_or_create_collection(
        name=settings.chroma_collection,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    col.upsert(
        ids=[c["id"] for c in chunks],
        documents=[c["document"] for c in chunks],
        metadatas=[c["metadata"] for c in chunks],
    )
    print(f"Upserted {len(chunks)} chunks → {settings.chroma_persist_dir!r}")
    print(f"Collection total: {col.count()}")


if __name__ == "__main__":
    main()


