import { loadSnapshot } from "@/lib/broker";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const POLL_MS = 1000;

export async function GET(request: Request): Promise<Response> {
  const encoder = new TextEncoder();
  let closed = false;
  const abort = () => {
    closed = true;
  };
  request.signal.addEventListener("abort", abort);

  const stream = new ReadableStream({
    async start(controller) {
      try {
        while (!closed) {
          const snapshot = await loadSnapshot();
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify(snapshot)}\n\n`),
          );
          await wait(POLL_MS, request.signal, () => closed);
        }
      } catch {
        closed = true;
      } finally {
        request.signal.removeEventListener("abort", abort);
        try {
          controller.close();
        } catch {
          return;
        }
      }
    },
    cancel() {
      closed = true;
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      Connection: "keep-alive",
    },
  });
}

function wait(
  ms: number,
  signal: AbortSignal,
  done: () => boolean,
): Promise<void> {
  return new Promise((resolve) => {
    if (done() || signal.aborted) {
      resolve();
      return;
    }
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish);
  });
}
