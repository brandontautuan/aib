const API = 'http://127.0.0.1:8000/api'

export type Result = { id: number; company: string; ceo?: string; source_type: string; publisher?: string; title: string; url: string; published_at?: string; description?: string; content?: string; transcript?: string }
export type Job = { id: string; status: 'queued' | 'running' | 'completed' | 'failed'; message: string; errors: string[]; found: number; saved: number }

export async function startScrape(payload: {company: string; ceo: string; sources: string[]}): Promise<Job> {
  const response = await fetch(`${API}/scrape`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)})
  if (!response.ok) throw new Error((await response.json()).detail || 'Could not start scrape')
  return response.json()
}
export const getJob = (id: string): Promise<Job> => fetch(`${API}/jobs/${id}`).then(r => r.json())
export const getResults = (company?: string): Promise<Result[]> => fetch(`${API}/results${company ? `?company=${encodeURIComponent(company)}` : ''}`).then(r => r.json())
export const exportUrl = (company?: string) => `${API}/export.csv${company ? `?company=${encodeURIComponent(company)}` : ''}`
