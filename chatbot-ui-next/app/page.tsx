import { ChatPanel } from "@/components/ChatPanel";

export default function HomePage() {
  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-6 py-4 shadow-sm">
        <h1 className="text-lg font-semibold text-gray-900">
          Trợ lý pháp luật giao thông
        </h1>
        <p className="text-xs text-gray-400 mt-0.5">
          Luật Trật tự, An toàn Giao thông Đường bộ 36/2024/QH15
        </p>
      </header>

      <main className="flex-1 flex flex-col max-w-3xl w-full mx-auto">
        <ChatPanel />
      </main>
    </div>
  );
}
