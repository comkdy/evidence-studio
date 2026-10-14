import type { Answer, Paper } from './types';
import { buildContributions, defaultExperts } from './experts';
import { captureAnswerEvidence } from './answerSelection';
import fixtures from "../shared/papers.json";
// Shared fictional interaction-design data, not real publications.
export const papers: Paper[] = fixtures;
export const incoming: Paper[] = [
  { ...papers[2], id:'p7', title:'Stack-Level Thermal Analysis for HBM', authors:'D. Moon, J. Son', year:2025, pages:15, chunks:34, filename:'hbm_stack_thermal.pdf' },
  { ...papers[1], id:'p8', title:'Retention Windows in Multilevel NAND', authors:'H. Shin, S. Jang', year:2025, pages:19, chunks:44, filename:'nand_retention_windows.pdf' },
];
export const members = [
  { id:'me', name:'나', initial:'나', role:'관리자' },
  { id:'jimin', name:'지민', initial:'지', role:'편집자' },
  { id:'seoyeon', name:'서연', initial:'서', role:'편집자' },
  { id:'doyun', name:'도윤', initial:'도', role:'멤버' },
];
const conversationFixtures: Answer[] = [
  { id:'example-team', question:'HBM의 thermal conditions와 packaging 구조를 비교해 줘', mode:'compare', paperIds:['p3','p4'], authorId:'jimin', visibility:'team', experts:['HBM · Packaging','Reliability'], expertSelection:'auto' },
  { id:'example-mine', question:'DRAM의 retention과 refresh 조건을 정리해 줘', mode:'summary', paperIds:['p1','p6'], authorId:'me', visibility:'team', experts:['DRAM','Reliability'], expertSelection:'auto' },
  { id:'example-private', question:'NAND reliability 평가에서 확인할 조건은?', mode:'limits', paperIds:['p2'], authorId:'me', visibility:'private', experts:['NAND','Reliability'], expertSelection:'auto' },
];
export const exampleConversations:Answer[]=conversationFixtures.map(answer=>{
  const contributions=buildContributions(answer.question,papers.filter(paper=>answer.paperIds.includes(paper.id)),defaultExperts.filter(expert=>answer.experts?.includes(expert.name)));
  const evidence=captureAnswerEvidence(contributions.flatMap(expert=>expert.claimMatches));
  return {...answer,paperIds:[...new Set(evidence.claims.map(claim=>claim.paperId))],experts:contributions.filter(expert=>expert.paperIds.length).map(expert=>expert.name),contributions,evidence};
});
export const suggestions = [
  { mode:'summary' as const, title:'HBM 수치 확인', text:'HBM bandwidth의 측정값과 조건은?', desc:'질문에 맞는 주장과 연결 수치를 확인하세요' },
  { mode:'summary' as const, title:'조건을 좁혀 질문', text:'85 °C에서 DRAM retention 측정값은?', desc:'온도 조건에 맞는 근거만 골라 보세요' },
  { mode:'claims' as const, title:'저자의 해석 확인', text:'NAND 열화 메커니즘에 대한 저자의 해석은?', desc:'해석에 직접 연결된 실험이 있는지 살펴보세요' },
];
