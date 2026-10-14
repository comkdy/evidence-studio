import type { ResearchMode, SavedSearch, SearchResult } from './pilotData';

export const researchModeLabels:Record<ResearchMode,string>={basic:'기본',deep:'심층'};

export function savedResearchMode(record:SavedSearch):ResearchMode {
 return record.result.research?.mode??record.mode??'basic';
}

export function answerLimitations(result:SearchResult):string[] {
 return [...new Set((result.research?.limitations??[]).filter(value=>value.trim()))];
}

// Reuse the saved response, including its caveats, for both current and historical answers.
export function answerCopyText(question:string,experts:string[],result:SearchResult,evidenceText:string[]):string {
 const limitations=answerLimitations(result);
 return [question,'참여 전문가: '+experts.join(', '),
  ...(result.research?[researchModeLabels[result.research.mode]+' 질의 · 검색한 논문 '+result.research.paper_count+'편']:[]),
  ...(result.synthesis?[result.synthesis.mode==='llm'?'종합 판단 · 근거 기반 답변':'검색한 근거 · 종합 답변 미생성',result.synthesis.markdown,...(result.synthesis.notice?[result.synthesis.notice]:[])]:[]),
  ...(limitations.length?['답변의 범위와 한계',...limitations.map(item=>'- '+item)]:[]),
  ...(result.answers??[]).flatMap(answer=>['전문가 관점: '+answer.expert_name,answer.mode==='llm'?'근거 기반 답변':answer.mode==='pending'?'지식 구성 중':'검색한 근거 · 답변 미생성',answer.markdown,...(answer.notice?[answer.notice]:[])]),
  ...(result.note?[result.note]:[]),...evidenceText,
 ].join('\n\n');
}
