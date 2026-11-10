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
    <aside aria-label="Turtle fact" className="relative min-w-0 flex-1 rounded-xl border border-line bg-surface py-3 pr-11 pl-4">
      <div className="text-xs tracking-wide text-muted uppercase">Turtle fact</div>
      <p key={`${index}-${turn}`} className="fact-fade mt-1.5 text-sm leading-relaxed text-pretty">
        {FACTS[index]}
      </p>
      <button
        type="button"
        onClick={next}
        aria-label="Next fact"
        title="Next fact"
        className="absolute right-2 bottom-2 flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:text-text focus-visible:outline-2 focus-visible:outline-accent"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round" className="h-4 w-4" aria-hidden="true">
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
      </button>
    </aside>
  )
}
