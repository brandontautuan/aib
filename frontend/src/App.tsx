import { FormEvent, useEffect, useState } from 'react'
import { exportUrl, getJob, getResults, Job, Result, startScrape } from './api'

const options = [
  ['youtube', 'YouTube interviews'], ['wsj', 'WSJ'], ['bloomberg', 'Bloomberg'],
  ['nyt', 'The New York Times'], ['economist', 'The Economist'], ['forbes', 'Forbes'],
] as const

export default function App() {
  const [company, setCompany] = useState('')
  const [ceo, setCeo] = useState('')
  const [sources, setSources] = useState<string[]>(['youtube', 'forbes'])
  const [job, setJob] = useState<Job | null>(null)
  const [results, setResults] = useState<Result[]>([])
  const [error, setError] = useState('')

  useEffect(() => { getResults().then(setResults).catch(() => {}) }, [])
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return
    const timer = window.setInterval(async () => {
      const update = await getJob(job.id); setJob(update)
      if (['completed', 'failed'].includes(update.status)) {
        clearInterval(timer)
        setResults(await getResults(company.trim()))
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [job?.id, job?.status])

  const toggle = (key: string) => setSources(items => items.includes(key) ? items.filter(x => x !== key) : [...items, key])
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('')
    if (!company.trim() || !sources.length) { setError('Enter a company and select at least one source.'); return }
    try { setJob(await startScrape({company: company.trim(), ceo: ceo.trim(), sources})) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not start scrape.') }
  }
  const busy = job && ['queued', 'running'].includes(job.status)
  return <main>
    <header><p className="eyebrow">LOCAL RESEARCH TOOL</p><h1>CEO + AI source finder</h1><p>Collect publicly accessible interviews and reporting. No AI classification, no paywall bypassing.</p></header>
    <section className="card"><form onSubmit={submit}>
      <label>Company<input value={company} onChange={e => setCompany(e.target.value)} placeholder="e.g. Microsoft" /></label>
      <label>CEO <span>optional</span><input value={ceo} onChange={e => setCeo(e.target.value)} placeholder="e.g. Satya Nadella" /></label>
      <fieldset><legend>Search sources</legend><div className="choices">{options.map(([key, label]) => <label className="choice" key={key}><input type="checkbox" checked={sources.includes(key)} onChange={() => toggle(key)} />{label}</label>)}</div></fieldset>
      {error && <p className="error">{error}</p>}<button disabled={!!busy}>{busy ? 'Scraping…' : 'Start scraping'}</button>
    </form></section>
    {job && <section className={`status ${job.status}`}><strong>{job.message}</strong>{job.errors.length > 0 && <details><summary>{job.errors.length} source notice{job.errors.length === 1 ? '' : 's'}</summary><ul>{job.errors.map((notice, i) => <li key={i}>{notice}</li>)}</ul></details>}</section>}
    <section className="results"><div className="results-head"><div><p className="eyebrow">COLLECTED SOURCES</p><h2>{results.length} result{results.length === 1 ? '' : 's'}</h2></div><a className="export" href={exportUrl(company.trim() || undefined)}>Export CSV</a></div>
      <div className="table-wrap"><table><thead><tr><th>Source</th><th>Title & content</th><th>Published</th></tr></thead><tbody>{results.map(item => <tr key={item.id}><td><b>{item.publisher || item.source_type}</b><small>{item.source_type}</small></td><td><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a>{(item.description || item.content || item.transcript) && <details><summary>View collected content</summary><p>{item.description}</p><pre>{item.content || item.transcript}</pre></details>}</td><td>{item.published_at || '—'}</td></tr>)}{!results.length && <tr><td colSpan={3} className="empty">Run a scrape to see saved sources here.</td></tr>}</tbody></table></div>
    </section>
  </main>
}
