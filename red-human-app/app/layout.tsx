import type { Metadata, Viewport } from "next";
import { Inter_Tight, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const display = Inter_Tight({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
  // Sin `weight`: Inter Tight es fuente variable (100-900) y cubre 400-800. Con la lista explícita, next/font/google
  // truena en CI («Cannot read properties of null (reading '1')»).
});

const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Red Human AI — Plataforma de RH con Inteligencia Artificial",
  description:
    "Agente integral de IA para todo el ciclo de vida del colaborador: reclutamiento, contratación, atención, capacitación, desempeño y clima.",
};

export const viewport: Viewport = {
  themeColor: "#f8f8f8",
  width: "device-width",
  initialScale: 1,
};

const themeScript = `
(function(){
  try {
    var t = localStorage.getItem('rh-theme');
    if (t === 'dark') { document.documentElement.classList.add('dark'); }
    else { document.documentElement.classList.remove('dark'); }
  } catch(e){ document.documentElement.classList.remove('dark'); }
})();
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" suppressHydrationWarning className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen bg-bg text-ink antialiased">{children}</body>
    </html>
  );
}
