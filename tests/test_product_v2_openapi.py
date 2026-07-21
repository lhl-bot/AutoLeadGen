import json
from pathlib import Path

from main import app


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
OPENAPI_SNAPSHOT = Path(__file__).parents[1] / "frontend" / "openapi.v2.json"


def _operations(schema, *, include_login=False):
    for path, path_item in schema["paths"].items():
        if not path.startswith("/api/v2") and not (include_login and path == "/api/auth/login"):
            continue
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield method, path, operation


def test_checked_in_openapi_snapshot_matches_the_v2_application_contract():
    # GIVEN: Frontend types are generated from the checked-in V2 contract.
    checked_in = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))

    # WHEN/THEN: Backend schema changes must update that contract before the
    # generated TypeScript layer can be accepted.
    assert checked_in == app.openapi()


def test_v2_openapi_has_typed_success_and_authentication_responses():
    schema = app.openapi()
    operations = list(_operations(schema))

    assert operations
    for method, path, operation in operations:
        unauthorized = operation["responses"].get("401")
        assert unauthorized is not None, f"{method.upper()} {path} is missing its 401 contract"
        assert unauthorized["content"]["application/json"]["schema"], (
            f"{method.upper()} {path} has an untyped 401 response"
        )

        success_responses = [
            response
            for code, response in operation["responses"].items()
            if code.startswith("2")
        ]
        assert success_responses, f"{method.upper()} {path} has no success response"
        for response in success_responses:
            assert response["content"]["application/json"]["schema"], (
                f"{method.upper()} {path} has an untyped success response"
            )


def test_openapi_declares_domain_error_responses_where_the_api_raises_them():
    schema = app.openapi()

    expected_codes = {
        ("get", "/api/v2/companies/{company_id}"): {"401", "404"},
        ("delete", "/api/v2/companies/{company_id}"): {"401", "404", "409"},
        ("post", "/api/v2/lists/{list_id}/memberships"): {"401", "404", "409"},
        ("post", "/api/v2/campaigns/{campaign_id}/start"): {"401", "404", "409"},
        ("patch", "/api/v2/tasks/{task_id}"): {"401", "404", "409"},
        ("post", "/api/v2/consent-restrictions"): {"401", "403", "404", "409"},
        ("post", "/api/auth/login"): {"401", "403", "429"},
    }

    for (method, path), codes in expected_codes.items():
        responses = schema["paths"][path][method]["responses"]
        assert codes.issubset(responses), f"{method.upper()} {path} is missing {codes - responses.keys()}"
        for code in codes:
            assert responses[code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


def test_login_and_v2_command_results_have_concrete_success_schemas():
    schema = app.openapi()
    expected_schema_refs = {
        ("post", "/api/auth/login", "200"): "LoginResponse",
        ("delete", "/api/v2/companies/{company_id}", "200"): "ArchiveResult",
        ("delete", "/api/v2/contacts/{contact_id}", "200"): "ArchiveResult",
        ("post", "/api/v2/lists/{list_id}/memberships", "201"): "MembershipRead",
        ("delete", "/api/v2/campaigns/{campaign_id}", "200"): "ArchiveResult",
        ("get", "/api/v2/providers/usage", "200"): "ProviderUsageRead",
        ("get", "/api/v2/analytics/outcomes", "200"): "OutcomeAnalyticsRead",
    }

    for (method, path, code), component in expected_schema_refs.items():
        response_schema = schema["paths"][path][method]["responses"][code]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{component}"}
