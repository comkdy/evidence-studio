"""Resolve private prior answer runs; dialogue is context, never new evidence."""
from fastapi import HTTPException
from .answer_trace import read_run


def conversation_context(run_ids):
    if len(run_ids) > 6 or len(set(run_ids)) != len(run_ids):
        raise HTTPException(422, '이전 대화는 중복 없이 최근 답변 6개까지 참조할 수 있습니다.')
    turns = []
    for run_id in run_ids:
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 80:
            raise HTTPException(422, '이전 답변 식별자를 확인해 주세요.')
        run = read_run(run_id)  # Owner check applies even to administrators.
        if run['status'] not in ('completed', 'evidence_only'):
            raise HTTPException(409, '완료된 이전 답변만 대화 문맥으로 사용할 수 있습니다.')
        result = run['result']
        summary = (result.get('synthesis') or {}).get('markdown', '')
        if not summary:
            summary = '\n'.join(a.get('markdown', '') for a in result.get('answers', []))
        turns.append({'question': run['question'][:1000], 'answer': summary[:2200]})
    return turns


def contextual_query(question, conversation):
    if not conversation:
        return question
    previous = ' / '.join(t['question'] for t in conversation[-2:])
    return question[:600] + '\n이전 질문의 주제: ' + previous[-380:]
