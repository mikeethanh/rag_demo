"use client";

interface Doc {
  title?: string;
  content?: string;
  source: string;
  page: string;
}

interface Props {
  docs: Doc[];
}

export function Citations({ docs }: Props) {
  if (!docs.length) return null;

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        Nguồn tham khảo
      </p>
      <ul className="space-y-2">
        {docs.map((doc, i) => (
          <li
            key={i}
            className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 text-xs text-gray-600"
          >
            {doc.title && (
              <p className="font-medium text-gray-700 mb-1">{doc.title}</p>
            )}
            {doc.content && (
              <p className="line-clamp-3 leading-relaxed">{doc.content}</p>
            )}
            {doc.source && (
              <p className="mt-1 font-medium text-gray-400">
                📄 {doc.source}
                {doc.page ? ` · trang ${doc.page}` : ""}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
