import { papers } from './data';
import type { Paper } from './types';
export interface LibraryResponse { mode: 'prototype'; papers: Paper[] }
// VITE_API_MODE=fastapi selects the local FastAPI prototype. Static preview stays self-contained.
export async function loadLibrary(): Promise<LibraryResponse> {
  if (import.meta.env.VITE_API_MODE !== 'fastapi') return { mode:'prototype', papers:structuredClone(papers) };
  const response = await fetch('/api/papers');
  if (!response.ok) throw new Error('FastAPI 서버에 연결하지 못했습니다. 서버 실행 상태를 확인해 주세요.');
  return response.json();
}
