"""KYC tools: applications with their documents, vendor checks, vendor outages."""

from toolsvc.models.fake_db import Where, ago, query
from toolsvc.tools.registry import Param, ToolError, tool

CHECK_TYPES = ("PAN_VERIFY", "AADHAAR_OTP", "FACE_MATCH", "LIVENESS", "SANCTIONS")


@tool(
    "Get all of a user's KYC attempts, newest first, each with its documents (doc_type, verification_status, "
    "failure_reason, name_on_doc, dob_on_doc). Shows rejection_reason_code and attempt_number (max 3 attempts). "
    "SANCTIONS_HIT is Compliance-only: never disclose it.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
)
def get_kyc_applications(user_id):
    applications = query("SELECT kyc_id, status, submitted_at, reviewed_at, rejection_reason_code, "
                         "review_queue, attempt_number FROM kyc_applications "
                         "WHERE user_id = :user_id ORDER BY submitted_at DESC", {"user_id": user_id})
    for application in applications:
        application["documents"] = query(
            "SELECT doc_type, verification_status, failure_reason, name_on_doc, dob_on_doc, uploaded_at "
            "FROM kyc_documents WHERE kyc_id = :kyc_id ORDER BY uploaded_at DESC",
            {"kyc_id": application["kyc_id"]})
    return applications


@tool(
    "List the KYC vendor checks for one application (PAN verify, Aadhaar OTP, face match, liveness, sanctions), "
    "newest first. A response_code of 5xx or TIMEOUT means the vendor failed, not the customer.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    kyc_id=Param("integer", "kyc_id from get_kyc_applications", required=True),
    check_types=Param("array", f"Only these check types: {', '.join(CHECK_TYPES)}", items="string"),
    limit=Param("integer", "Return at most this many rows (default 20)"),
)
def get_kyc_vendor_logs(user_id, kyc_id, check_types=None, limit=None):
    is_theirs = query("SELECT 1 FROM kyc_applications WHERE kyc_id = :kyc_id AND user_id = :user_id",
                      {"kyc_id": kyc_id, "user_id": user_id})
    if not is_theirs:  # another customer's application looks exactly like one that doesn't exist
        raise ToolError(f"KYC application {kyc_id} not found")
    where = Where().add("kyc_id = :kyc_id", kyc_id=kyc_id).in_list("check_type", check_types)
    return query(f"SELECT check_type, response_code, response_message, latency_ms, created_at "
                 f"FROM kyc_vendor_logs{where.sql()} ORDER BY created_at DESC LIMIT :limit",
                 {**where.params, "limit": limit or 20})


@tool(
    "Check KYC vendor health across ALL users: failed vendor calls (5xx or TIMEOUT) per check type in a recent "
    "window. Several failures = a vendor outage (kyc_sop KYC-14): the customer did nothing wrong.",
    minutes=Param("integer", "Window in minutes (default 60)"),
)
def get_kyc_vendor_health(minutes=None):
    window = minutes or 60
    failures = query("SELECT check_type, count(*) AS failures FROM kyc_vendor_logs "
                     "WHERE (response_code LIKE '5%' OR response_code = 'TIMEOUT') AND created_at >= :since "
                     "GROUP BY check_type ORDER BY failures DESC", {"since": ago(minutes=window)})
    return {"window_minutes": window, "failures_by_check": failures}
