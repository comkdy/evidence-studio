"""Load one maintained policy plus a small task contract at operation time."""
from dataclasses import dataclass
from pathlib import Path
from .wiki_store import WikiError, digest

PROMPTS = Path(__file__).with_name('prompts')
POLICY_PATH = PROMPTS / 'wiki_policy.md'
CONTRACTS = {'compile': 'wiki_compile.txt', 'answer': 'wiki_answer.txt', 'lint': 'wiki_lint.txt'}
CONTRACTS.update(plan='research_plan.txt', refine='research_refine.txt', review='research_review.txt')


@dataclass(frozen=True)
class WikiPrompt:
    text: str
    policy_hash: str
    fingerprint: str


def load_prompt(operation):
    """Load edits on the next operation; a batch holds one consistent snapshot."""
    if operation not in CONTRACTS:
        raise WikiError('지원하지 않는 Wiki 작업입니다.')
    try:
        policy = POLICY_PATH.read_text(encoding='utf-8').strip()
        contract = (PROMPTS / CONTRACTS[operation]).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        raise WikiError('공통 Wiki 지침 또는 작업 프롬프트를 읽을 수 없습니다. backend/prompts를 확인하세요.') from None
    if not policy or not contract:
        raise WikiError('공통 Wiki 지침과 작업 프롬프트는 비어 있을 수 없습니다.')
    text = policy + '\n\n---\n\n' + contract
    return WikiPrompt(text, digest(policy), digest(text))
