"use client";

import { useEffect } from "react";

export default function AdminIngestionError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 에러 로깅 (실제 환경에서는 Sentry 등으로 전송)
    console.error("[AdminIngestionPage]", error);
  }, [error]);

  return (
    <div className="max-w-md mx-auto mt-16 text-center space-y-4">
      <div className="rounded-md border border-red-200 bg-red-50 p-6 space-y-3">
        <h2 className="text-sm font-semibold text-red-700">
          파이프라인 상태를 불러오지 못했습니다
        </h2>
        <p className="text-xs text-red-600">{error.message}</p>
        {error.digest && (
          <p className="text-xs text-red-400">오류 코드: {error.digest}</p>
        )}
      </div>
      <button
        onClick={reset}
        className="px-4 py-2 text-sm font-medium rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 transition-colors"
      >
        다시 시도
      </button>
    </div>
  );
}
