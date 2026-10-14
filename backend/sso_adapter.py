"""Replace these two functions after the internal SSO/API contract is confirmed.

Never parse an unverified employee ID out of a browser cookie. Validate the
cookie with the company SSO (or verify its signature, issuer, audience, expiry)
first. Do not log or persist the raw cookie. Profile API credentials stay here
on the backend; no browser-supplied profile or role is trusted.
"""
from dataclasses import dataclass
from fastapi import HTTPException


@dataclass(frozen=True)
class EmployeeProfile:
    employee_id: str
    name: str
    team: str


def verify_sso_cookie(cookie: str) -> str:
    """Return only the employee ID authenticated by the company SSO."""
    raise HTTPException(503, '사내 SSO 검증 연결을 준비 중입니다. 관리자에게 문의해 주세요.')


def fetch_employee_profile(employee_id: str) -> EmployeeProfile:
    """Call the internal directory API using the verified employee ID."""
    raise HTTPException(503, '사내 사용자 프로필 API 연결을 준비 중입니다.')
