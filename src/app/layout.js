import "./globals.css";

export const metadata = {
  title: "Varyn | AI Risk Intelligence Command System",
  description:
    "A voice-enabled AI risk intelligence command system for financial, operational, market, credit, and liquidity risk analysis.",
  openGraph: {
    title: "Varyn | AI Risk Intelligence Command System",
    description:
      "Institutional-grade risk intelligence through conversational AI, live monitoring, analytics, and executive-ready reporting.",
    type: "website",
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
