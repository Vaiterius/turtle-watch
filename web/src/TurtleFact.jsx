import { useEffect, useState } from 'react'
import FACTS from './facts.json'

const ROTATE_MS = 7000

// Any fact except the current one, so the same fact never shows twice in a row.
const pick = (current) => {
  if (FACTS.length < 2) return 0
  const next = Math.floor(Math.random() * (FACTS.length - 1))
  return next >= current ? next + 1 : next
}

export default function TurtleFact() {
  const [index, setIndex] = useState(() => pick(-1))
  const [turn, setTurn] = useState(0) // bumps on Next so the timer restarts

  useEffect(() => {
    const timer = setTimeout(() => setIndex((i) => pick(i)), ROTATE_MS)
    return () => clearTimeout(timer)
  }, [index, turn])

  const next = () => {
    setIndex((i) => pick(i))
    setTurn((t) => t + 1)
  }

  return (
    <aside aria-label="Turtle facts" className="flex flex-col gap-1 border-t border-line pt-2.5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm text-muted">Turtle facts</h2>
        <button
          type="button"
          onClick={next}
          className="-my-2 -mr-0.5 flex min-h-11 items-center gap-1.5 pl-3 text-sm font-semibold transition-opacity hover:opacity-70 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
        >
          Next fact
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round"
            strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </button>
      </div>
      <p key={`${index}-${turn}`} className="fact-fade text-[15px] leading-normal text-pretty">
        {FACTS[index]}
      </p>
    </aside>
  )
}
