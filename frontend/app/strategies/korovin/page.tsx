import Link from "next/link";
import KorovinPanel from "../../components/KorovinPanel";

export default function KorovinPage() {
  return <main className="min-h-screen bg-slate-950 text-white">
    <header className="border-b border-slate-800 p-4">
      <div className="mx-auto max-w-7xl">
        <Link href="/" className="text-sm text-sky-300 hover:underline">← Все стратегии</Link>
        <h1 className="mt-3 text-2xl font-bold">Прикрытый интрадей · BTC</h1>
        <p className="mt-1 text-sm text-slate-400">Виртуальная торговля · данные Bybit</p>
      </div>
    </header>
    <div className="mx-auto max-w-7xl px-2 sm:px-4"><KorovinPanel /></div>
  </main>;
}
