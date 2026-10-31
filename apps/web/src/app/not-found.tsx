import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main" className="center-page">
      <div className="card">
        <h1>Page not found</h1>
        <p className="muted">This address does not exist in the Zeoatap dashboard.</p>
        <Link href="/" className="btn btn-primary">
          Go to the overview
        </Link>
      </div>
    </main>
  );
}
