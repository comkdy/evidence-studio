import fixtures from '../shared/evidence.json' with { type: 'json' };
import type { Claim, EvidenceDataset } from './types';

export const evidenceData = fixtures as EvidenceDataset;
export const kindLabels = { result:'실험 결과', interpretation:'저자의 해석', hypothesis:'가설', limitation:'한계' };
export const getClaims = (paperId:string) => evidenceData.claims.filter(claim=>claim.paperId===paperId);
export const getAnswerClaims = (paperId:string) => getClaims(paperId).filter(claim=>claim.validation==='passed'&&claim.attribution==='authors');
export const getMeasurements = (paperId:string) => evidenceData.measurements.filter(item=>item.paperId===paperId);
export const getClaimMeasurements = (claims:Claim[]) => evidenceData.measurements.filter(item=>claims.some(claim=>claim.paperId===item.paperId&&claim.measurementIds.includes(item.id)));
export const getSpan = (id:string) => evidenceData.spans.find(span=>span.id===id);
export const getPrimarySpan = (paperId:string) => evidenceData.spans.find(span=>span.paperId===paperId);
