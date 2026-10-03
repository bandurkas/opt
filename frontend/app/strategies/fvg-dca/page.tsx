import Link from "next/link";
import FvgDcaPanel from "../../components/FvgDcaPanel";

export default function FvgDcaPage(){
  return <main className="w-full min-w-0 min-h-screen bg-slate-950 text-slate-100">
    <header className="border-b border-slate-800 px-4 py-3">
      <div className="max-w-6xl mx-auto flex flex-wrap items-center justify-between gap-3">
        <div><h1 className="text-xl font-bold">FVG A · 1D / 1W</h1>
          <p className="text-xs text-slate-500">Виртуальные сделки после уведомлений; не торговый счёт</p></div>
        <Link href="/" className="text-sm text-cyan-300 hover:underline">← На главную</Link>
      </div>
    </header>
    <div className="max-w-6xl mx-auto p-4"><FvgDcaPanel/></div>
  </main>;
}
