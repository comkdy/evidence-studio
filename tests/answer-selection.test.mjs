import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { buildContributions, defaultExperts } from '../src/experts.ts';
import { answerPlainText, captureAnswerEvidence, selectedStatement } from '../src/answerSelection.ts';
import { evidenceData } from '../src/evidence.ts';

const papers=JSON.parse(await readFile(new URL('../shared/papers.json',import.meta.url),'utf8'));
function answerFor(question,experts=defaultExperts,library=papers){
  const contributions=buildContributions(question,library,experts);
  const evidence=captureAnswerEvidence(contributions.flatMap(item=>item.claimMatches));
  return {id:'test',question,mode:question.includes('비교')?'compare':'summary',paperIds:[...new Set(evidence.claims.map(claim=>claim.paperId))],authorId:'me',visibility:'team',experts:contributions.filter(item=>item.paperIds.length).map(item=>item.name),contributions,evidence};
}
const claimIds=answer=>answer.evidence.claims.map(item=>item.id);

test('the same HBM expert selects bandwidth or thermal claims according to the question',()=>{
  const experts=[defaultExperts[2]];
  const bandwidth=answerFor('HBM bandwidth의 측정값과 조건은?',experts);
  assert.deepEqual(claimIds(bandwidth),['p4-result']);
  assert.deepEqual(bandwidth.evidence.measurements.map(item=>item.id),['p4-measure']);
  assert.deepEqual(bandwidth.paperIds,['p4']);
  assert.equal(selectedStatement(bandwidth,'p4').id,'p4-table');
  const thermal=answerFor('HBM thermal 측정값은?',experts);
  assert.deepEqual(claimIds(thermal),['p3-result']);
  assert.deepEqual(thermal.evidence.measurements.map(item=>item.id),['p3-measure']);
});

test('temperature and voltage narrow claims; an unknown or mismatched condition withholds evidence',()=>{
  assert.deepEqual(claimIds(answerFor('85 °C에서 DRAM retention 측정값은?')),['p1-result']);
  assert.deepEqual(claimIds(answerFor('DRAM retention 125 °C 1.1 V 수치는?')),['p6-result']);
  assert.deepEqual(claimIds(answerFor('DRAM retention 85 °C 1.2 V 수치는?')),[]);
  assert.deepEqual(claimIds(answerFor('DRAM retention 60 °C 수치는?')),[]);
  const comparison=answerFor('DRAM retention 조건을 비교해 줘');
  assert.deepEqual(new Set(claimIds(comparison)),new Set(['p1-result','p6-result']));
  assert.equal(comparison.evidence.relations[0].comparisonStatus,'different-conditions');
  assert.match(answerPlainText(comparison,papers),/직접 비교 보류/);
});

test('interpretation uses its own quotation and never inherits the papers unlinked endurance number',()=>{
  const answer=answerFor('NAND 열화 메커니즘에 대한 저자의 해석은?');
  assert.deepEqual(claimIds(answer),['p2-interpretation']);
  assert.deepEqual(answer.evidence.measurements,[]);
  assert.equal(selectedStatement(answer,'p2').id,'p2-discussion');
  const text=answerPlainText(answer,papers);
  assert.match(text,/이 주장에 직접 연결된 수치 없음/);
  assert.match(text,/p\. 10/);
  assert.doesNotMatch(text,/3,000|p\. 8/);
  assert.ok(answer.contributions.every(item=>!item.measurementIds.length));
});

test('result values retain inequalities and unknown conditions in the copied answer',()=>{
  const endurance=answerPlainText(answerFor('NAND endurance 측정값은?'),papers);
  assert.match(endurance,/≥ 3,000/);
  const bandwidth=answerPlainText(answerFor('HBM bandwidth 측정값은?'),papers);
  assert.match(bandwidth,/128 GB\/s/);
  assert.match(bandwidth,/미기재/);
  assert.doesNotMatch(bandwidth,/92 °C/);
});

test('claim selection honors expert admission, review and explicit paper scope without fallback',()=>{
  assert.deepEqual(claimIds(answerFor('HBM bandwidth 측정값은?',[defaultExperts[0]])),[]);
  assert.deepEqual(claimIds(answerFor('HBM bandwidth 측정값은?',defaultExperts,papers.filter(item=>item.id==='p3'))),[]);
  assert.deepEqual(claimIds(answerFor('HBM bandwidth 측정값은?',[{...defaultExperts[2],reviewIds:['p4']}])),[]);
  assert.deepEqual(claimIds(answerFor('ECC power 가설은?')),[]);
  assert.deepEqual(claimIds(answerFor('DRAM superconductivity 측정값은?')),[]);
  const pending={...papers[2],id:'p7'};
  assert.deepEqual(claimIds(answerFor('HBM thermal 측정값은?',[{...defaultExperts[2],paperIds:['p7']}],[pending])),[]);
});

test('answer snapshots preserve selected evidence and separate expert contributions after fixture changes',()=>{
  const answer=answerFor('85 °C DRAM retention 수치는?');
  assert.equal(answer.contributions.filter(item=>item.claimMatches.length).length,2);
  assert.equal(answer.evidence.claims.length,1);
  const claim=evidenceData.claims.find(item=>item.id==='p1-result');
  const number=evidenceData.measurements.find(item=>item.id==='p1-measure');
  const span=evidenceData.spans.find(item=>item.id==='p1-table');
  const oldClaim=claim.summary, oldNumber=number.rawValue, oldQuote=span.quote;
  const before=answerPlainText(answer,papers);
  try{
    claim.summary='changed';number.rawValue='999 ms';span.quote='changed source';
    assert.equal(answerPlainText(answer,papers),before);
    assert.equal(answer.evidence.claims[0].summary,oldClaim);
    assert.equal(answer.evidence.measurements[0].rawValue,oldNumber);
    assert.equal(selectedStatement(answer,'p1').quote,oldQuote);
  }finally{claim.summary=oldClaim;number.rawValue=oldNumber;span.quote=oldQuote;}
});

test('several measurements on one claim are narrowed by condition and unrelated links cannot enter a snapshot',()=>{
  const claim=evidenceData.claims.find(item=>item.id==='p1-result');
  const extra=structuredClone(evidenceData.measurements.find(item=>item.id==='p1-measure'));
  extra.id='p1-hot';extra.rawValue='40 ms';extra.conditions[0].rawValue='125 °C';
  evidenceData.measurements.push(extra);claim.measurementIds.push(extra.id);
  try{
    const answer=answerFor('DRAM retention 85 °C 수치는?');
    assert.deepEqual(answer.evidence.measurements.map(item=>item.id),['p1-measure']);
    assert.doesNotMatch(answerPlainText(answer,papers),/40 ms/);
    const snapshot=captureAnswerEvidence([{claimId:'p1-result',reason:'test',measurementIds:['p1-measure','p4-measure']}]);
    assert.deepEqual(snapshot.measurements.map(item=>item.id),['p1-measure']);
    assert.deepEqual(snapshot.matches[0].measurementIds,['p1-measure']);
  }finally{claim.measurementIds.pop();evidenceData.measurements.pop();}
});
