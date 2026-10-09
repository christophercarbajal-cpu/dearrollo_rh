"use client";

/* Sala pública de entrevista con agente IA (módulo 3.10).
   Con ANAM_API_KEY en la API → video con avatar; sin clave → chat de texto
   con el mismo agente. La evaluación final la revisa una persona de RH. */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  ShieldCheck,
  Sparkles,
  Send,
  Phone,
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  Video,
  MessageCircle,
  CheckCircle2,
  Loader2,
} from "lucide-react";
import { Logo, Button, Card, Badge } from "@/components/ui";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";
import { esTotem, perifericosDisponibles } from "@/lib/use-totem";
import {
  fetchEntrevistaPublica,
  consentirEntrevista,
  iniciarEntrevista,
  turnoEntrevista,
  finalizarEntrevista,
  finalizarEntrevistaBeacon,
  sincronizarTranscript,
  type CierreEntrevista,
  type EntrevistaPublica,
} from "@/lib/api";

type Fase = "cargando" | "no_disponible" | "cerrada" | "error" | "microfono" | "consentimiento" | "conectando" | "sala" | "finalizando" | "fin" | "interrumpida";

const ESTADOS_CERRADOS = ["completada", "evaluada", "interrumpida", "parcial"];
const MENSAJE_CERRADA = "Esta entrevista ya fue completada o interrumpida. Solicita a RH reabrirla.";
/* Si el avatar no manda SESSION_READY ni empieza a reproducir video en este tiempo, la sala cae a
   modo texto en vez de quedarse en negro (2026-09-13). 2026-09-14: subido a 60 s — Anam puede tardar
   más de 25 s en producción (arranque del motor + WebRTC) y el vigilante abortaba de más. */
const ESPERA_AVATAR_SEG = 60;
/* 2026-09-14 (demo en producción): DIAGNÓSTICO VISUAL. Con `true`, el avatar NO cae a texto cuando falla:
   el error crudo (código/razón de CONNECTION_CLOSED, error del SDK, timeout) se pinta en un bloque rojo en
   la sala, y arriba se muestra si el session_token llegó del backend, el contexto seguro (HTTPS) y la
   bitácora de eventos del SDK. Regresar a `false` cuando se encuentre la causa. */
const DIAGNOSTICO_AVATAR = false;
const MENSAJE_MICROFONO =
  "Para la entrevista en video necesitamos acceso a tu micrófono. Permítelo en tu navegador y vuelve a intentar, o continúa por chat.";
type Msg = { rol: "assistant" | "user"; texto: string };
/* Estado de la conexión del avatar: "conectando" hasta SESSION_READY / VIDEO_PLAY_STARTED; "listo" con
   video; "abandonado" cuando se cayó a texto. Distingue el CONNECTION_CLOSED que dispara nuestro propio
   stopStreaming() (al abandonar) del que manda Anam cuando la sesión real termina. */
type EstadoAvatar = "inactivo" | "conectando" | "listo" | "abandonado";

/** Espera a que React monte el elemento (el <video> aparece al pasar a "sala"); antes se asumía con
 * setTimeout(0) y el SDK podía fallar con "video element not found". */
async function esperarElemento(id: string, maxMs = 5000): Promise<boolean> {
  const inicio = Date.now();
  while (Date.now() - inicio < maxMs) {
    if (document.getElementById(id)) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
}

/* Fase 4 (Punto 3) — silencio. La regla del documento define la frase del inicio; la variante
   intermedia es un supuesto ajustable. Los tiempos son conservadores para no interrumpir a
   alguien que está pensando. */
const SILENCIO_INICIO_SEG = 12;
const SILENCIO_ENTREVISTA_SEG = 25;
const MAX_AVISOS_SILENCIO = 2;
/* Despedida fija de la entrevistadora — debe coincidir con ia.DESPEDIDA_ENTREVISTA en el backend (fallback
   "marcador" del cierre automático; el servidor la verifica de todos modos). */
const DESPEDIDA = "con esto terminamos la entrevista";

export default function SalaEntrevista() {
  const params = useParams();
  const token = String(params?.token ?? "");

  const [fase, setFase] = useState<Fase>("cargando");
  const [info, setInfo] = useState<EntrevistaPublica | null>(null);
  const [acepto, setAcepto] = useState(false);
  const [modo, setModo] = useState<"avatar" | "texto">("texto");
  /* Modo TÓTEM (2026-09-17): pantallas táctiles verticales de expo (55", 1080×1920). Se activa con
     `?totem=1` o solo cuando la pantalla es un retrato grande (no un celular): diseño inmersivo a
     pantalla completa, video llenando el área superior/central sin deformarse (object-cover) y
     controles enormes abajo (Hablar/Silenciar micrófono · Sonido · Terminar). */
  const [totem, setTotem] = useState(false);
  const [micActivo, setMicActivo] = useState(true);
  const [sonido, setSonido] = useState(true);
  const [mensajes, setMensajes] = useState<Msg[]>([]);
  const [texto, setTexto] = useState("");
  const [pensando, setPensando] = useState(false);

  const anamRef = useRef<{ stopStreaming?: () => Promise<void>; talk?: (t: string) => Promise<void> } | null>(null);
  const transcriptRef = useRef<Msg[]>([]);
  const chatRef = useRef<HTMLDivElement>(null);
  // evita finalizar dos veces: el clic manual en "Terminar" también dispara CONNECTION_CLOSED
  // como efecto de stopStreaming(), así que ambos caminos se guardan con la misma bandera.
  const finalizadoRef = useRef(false);
  // 2026-09-17: el transcript se sincroniza al servidor conforme avanza (debounce) — antes vivía solo
  // aquí hasta /finalizar y una pestaña cerrada / un historial vaciado por stopStreaming lo perdía todo.
  const syncRef = useRef<{ timer: ReturnType<typeof setTimeout> | null; enviados: number }>({ timer: null, enviados: 0 });
  const nombreRef = useRef("");
  const silencioRef = useRef<{ timer: ReturnType<typeof setTimeout> | null; avisos: number; hablando: boolean }>({ timer: null, avisos: 0, hablando: false });
  const [errorTexto, setErrorTexto] = useState("");
  const avatarRef = useRef<EstadoAvatar>("inactivo");
  const [errorAvatar, setErrorAvatar] = useState("");
  const [seguro, setSeguro] = useState<boolean | null>(null);
  const [debug, setDebug] = useState<{ modo?: string; token?: string; motivo?: string; eventos: string[] }>({ eventos: [] });
  const bitacora = useCallback((linea: string) => {
    const hora = new Date().toISOString().slice(11, 23);
    console.info("[avatar]", linea);
    setDebug((d) => ({ ...d, eventos: [...d.eventos, `${hora} ${linea}`].slice(-30) }));
  }, []);
  const microfonoRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    // Sin contexto seguro (HTTP que no sea localhost) el navegador NO expone getUserMedia y Anam no
    // puede ni pedir micrófono: el avatar es imposible. Se avisa en grande en vez de caer en silencio.
    const esSeguro = typeof window !== "undefined" && window.isSecureContext === true;
    setSeguro(esSeguro);
    if (!esSeguro) console.error("❌ Contexto NO seguro (HTTP): el avatar requiere HTTPS", window.location.href);
    // precalienta el chunk del SDK del avatar; el clic solo tiene que iniciar el stream
    import("@anam-ai/js-sdk").catch(() => {});
    fetchEntrevistaPublica(token)
      .then((i) => {
        if (!i) return setFase("no_disponible");
        setInfo(i);
        nombreRef.current = i.candidato;
        if (i.estado === "completada" || i.estado === "evaluada") return setFase("fin");
        if (i.estado === "interrumpida" || i.estado === "parcial") return setFase("interrumpida");
        setFase("consentimiento");
      })
      .catch(() => setFase("no_disponible"));
  }, [token]);

  /** 2026-09-13: la API rechazó consentimiento/sesión (409 cerrada, 403 sin consentimiento, red…).
   * Nunca dejamos la pantalla en blanco: se muestra un mensaje claro según el estado real. */
  const rechazoSesion = useCallback(
    async (detalle: string) => {
      const actual = await fetchEntrevistaPublica(token).catch(() => null);
      if (actual) setInfo(actual);
      if (!actual) return setFase("no_disponible");
      if (ESTADOS_CERRADOS.includes(actual.estado) || /cerrada|reabrir/i.test(detalle)) {
        setErrorTexto(MENSAJE_CERRADA);
        return setFase("cerrada");
      }
      setErrorTexto(detalle || "No pudimos iniciar tu entrevista. Intenta de nuevo en unos segundos.");
      setFase("error");
    },
    [token],
  );

  useEffect(() => {
    // Solo al montar (regla compartida en lib/use-totem.ts): si cambiara a mitad de la sesión, el <video>
    // se desmontaría y se perdería el stream.
    setTotem(esTotem());
  }, []);

  // 2026-09-21 (Tótem, periféricos USB): antes de iniciar se enumeran los micrófonos para avisar en grande si
  // Windows no expone ninguno; el prompt de permisos lo dispara `getUserMedia` al tocar «Iniciar».
  const [perifericos, setPerifericos] = useState<{ microfonos: number; camaras: number; soportado: boolean } | null>(null);
  useEffect(() => {
    if (!totem) return;
    void perifericosDisponibles().then(setPerifericos);
    const md = typeof navigator !== "undefined" ? navigator.mediaDevices : undefined;
    const alCambiar = () => void perifericosDisponibles().then(setPerifericos);
    md?.addEventListener?.("devicechange", alCambiar);
    return () => md?.removeEventListener?.("devicechange", alCambiar);
  }, [totem]);

  /** Tótem: micrófono (pista de audio local) y sonido del avatar (elemento de video). */
  const alternarMic = useCallback(() => {
    setMicActivo((v) => {
      const nuevo = !v;
      microfonoRef.current?.getAudioTracks().forEach((t) => (t.enabled = nuevo));
      return nuevo;
    });
  }, []);
  const alternarSonido = useCallback(() => {
    setSonido((v) => {
      const nuevo = !v;
      const video = document.getElementById("avatar-video") as HTMLVideoElement | null;
      if (video) video.muted = !nuevo;
      return nuevo;
    });
  }, []);

  /** Cierre único (Fase 4): cualquiera de los caminos (automático, botón, desconexión) pasa por aquí
   * UNA sola vez; el servidor verifica `cierre` contra el transcript y decide evaluada/interrumpida. */
  const cerrar = useCallback(
    async (cierre: CierreEntrevista, conTranscript: boolean) => {
      if (finalizadoRef.current) return;
      finalizadoRef.current = true;
      if (silencioRef.current.timer) clearTimeout(silencioRef.current.timer);
      setFase("finalizando");
      // Snapshot ANTES de parar el stream: el SDK puede emitir un MESSAGE_HISTORY_UPDATED vacío al
      // cerrar y dejaba el transcript en cero → «sin respuestas» aunque la entrevista fue completa.
      const transcript = [...transcriptRef.current];
      if (syncRef.current.timer) clearTimeout(syncRef.current.timer);
      try {
        await anamRef.current?.stopStreaming?.();
      } catch {}
      microfonoRef.current?.getTracks().forEach((t) => t.stop());
      microfonoRef.current = null;
      const r = await finalizarEntrevista(token, conTranscript ? transcript : undefined, cierre);
      setFase(r.ok && r.data.estado === "interrumpida" ? "interrumpida" : "fin");
    },
    [token],
  );

  /** Sincroniza el transcript al servidor (debounce 1.5 s). Nunca bloquea ni falla la sala. */
  const sincronizar = useCallback(() => {
    const st = syncRef.current;
    if (st.timer) clearTimeout(st.timer);
    st.timer = setTimeout(() => {
      if (finalizadoRef.current) return;
      const t = transcriptRef.current;
      if (t.length <= st.enviados) return;
      st.enviados = t.length;
      void sincronizarTranscript(token, t);
    }, 1500);
  }, [token]);

  /** Silencio (avatar): si la persona no habla, Red Human repite el aviso (ia.AVISO_SILENCIO); máximo 2 veces.
   * Antes del primer turno del candidato vuelve a preguntar «¿Comenzamos?»; después, pide repetir la respuesta. */
  const programarAvisoSilencio = useCallback(() => {
    const st = silencioRef.current;
    if (st.timer) clearTimeout(st.timer);
    const sinTurnoCandidato = !transcriptRef.current.some((m) => m.rol === "user");
    const seg = sinTurnoCandidato ? SILENCIO_INICIO_SEG : SILENCIO_ENTREVISTA_SEG;
    st.timer = setTimeout(async () => {
      if (finalizadoRef.current || st.hablando || st.avisos >= MAX_AVISOS_SILENCIO) return;
      st.avisos += 1;
      const nombre = nombreRef.current || "";
      const frase = sinTurnoCandidato
        ? `${nombre}, no te escuché. ¿Comenzamos?`
        : `${nombre}, no te escuché. ¿Me repites tu respuesta?`;
      try {
        await anamRef.current?.talk?.(frase);
      } catch {}
      programarAvisoSilencio();
    }, seg * 1000);
  }, []);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [mensajes, pensando]);

  /** Pestaña cerrada / navegación a mitad de la entrevista: se manda el cierre con sendBeacon
   * (sobrevive al unload). El servidor lo trata como `desconexion`; si no llega, el job de
   * inactividad cierra la entrevista con el transcript ya sincronizado. */
  useEffect(() => {
    const alSalir = () => {
      if (finalizadoRef.current || modo !== "avatar") return;
      if (!transcriptRef.current.some((m) => m.rol === "user")) return;
      finalizadoRef.current = true;
      finalizarEntrevistaBeacon(token, transcriptRef.current, "desconexion");
    };
    window.addEventListener("pagehide", alSalir);
    return () => window.removeEventListener("pagehide", alSalir);
  }, [token, modo]);

  /** Modo texto como red de seguridad: se pide al servidor una sesión de texto y se entra a la sala. */
  const entrarTexto = useCallback(
    async (s: { mensajes?: { rol: string; texto: string }[] } | null) => {
      let datos = s;
      if (!datos || !datos.mensajes) {
        const r = await iniciarEntrevista(token, true);
        if (!r.ok) return rechazoSesion(r.error);
        datos = r.data;
      }
      setModo("texto");
      const iniciales = (datos.mensajes ?? []).map((m) => ({ rol: m.rol as Msg["rol"], texto: m.texto }));
      setMensajes(iniciales);
      transcriptRef.current = iniciales;
      setFase("sala");
    },
    [token, rechazoSesion],
  );

  const empezar = useCallback(async () => {
    setFase("conectando");
    const ok = await consentirEntrevista(token);
    if (!ok.ok) return rechazoSesion(ok.error);
    const sesion = await iniciarEntrevista(token);
    if (!sesion.ok) return rechazoSesion(sesion.error);
    const s = sesion.data;
    if (s.modo === "texto" && s.motivo) console.warn("ℹ️ Entrevista en modo texto:", s.motivo);
    setDebug((d) => ({
      ...d,
      modo: s.modo,
      motivo: s.motivo || "",
      token: s.session_token ? `sí (${s.session_token.length} chars, ${s.session_token.slice(0, 12)}…)` : "VACÍO",
    }));
    bitacora(`POST /sesion → modo=${s.modo} session_token=${s.session_token ? "sí" : "NO"}${s.motivo ? " motivo=" + s.motivo : ""}`);
    if (DIAGNOSTICO_AVATAR && s.modo === "texto") {
      setErrorAvatar(`El backend NO regresó sesión de avatar (modo=texto). Motivo: ${s.motivo || "sin motivo"}`);
    }

    if (s.modo === "avatar" && s.session_token) {
      // 2026-09-14: el micrófono se pide ANTES de tocar el SDK. Si el candidato lo niega (o el navegador
      // no lo expone), el SDK cerraba la conexión al instante (MICROPHONE_PERMISSION_DENIED) y la sala
      // caía a texto sin explicar nada. Ahora se avisa y se puede reintentar o seguir por chat.
      let microfono: MediaStream | null = null;
      try {
        microfono = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true } });
      } catch (err) {
        console.error("❌ Micrófono no disponible:", err);
        bitacora(`getUserMedia falló: ${(err as Error)?.name ?? ""} ${(err as Error)?.message ?? String(err)}`);
        setErrorTexto(MENSAJE_MICROFONO + ` [${(err as Error)?.name ?? ""}: ${(err as Error)?.message ?? String(err)}]`);
        return setFase("microfono");
      }
      microfonoRef.current = microfono;
      bitacora("micrófono OK");
      try {
        const { createClient, AnamEvent } = await import("@anam-ai/js-sdk");
        const client = createClient(s.session_token);
        const anam = client as unknown as {
          stopStreaming?: () => Promise<void>;
          talk?: (t: string) => Promise<void>;
          addListener: (ev: string, cb: (...args: any[]) => void) => void;
        };
        anamRef.current = anam;
        if (s.nombre) nombreRef.current = s.nombre;
        avatarRef.current = "conectando";
        let vigilante: ReturnType<typeof setTimeout> | null = null;

        const avatarListo = (origen: string) => {
          if (avatarRef.current !== "conectando") return;
          avatarRef.current = "listo";
          if (vigilante) clearTimeout(vigilante);
          console.info("✅ Avatar listo (" + origen + ")");
          programarAvisoSilencio();
        };
        // Si el stream falla o nunca llega el video, la sala NO se queda en negro: se cae a texto con
        // una sesión forzada (2026-09-13). Solo mientras se estaba conectando y no se cerró.
        const describir = (m: unknown) => {
          if (m instanceof Error) {
            const extra = (m as unknown as { code?: unknown; status?: unknown; cause?: unknown; details?: unknown });
            return `${m.name}: ${m.message}` + (extra.code !== undefined ? ` code=${String(extra.code)}` : "") +
              (extra.status !== undefined ? ` status=${String(extra.status)}` : "") +
              (extra.cause !== undefined ? ` cause=${JSON.stringify(extra.cause)}` : "") +
              (extra.details !== undefined ? ` details=${JSON.stringify(extra.details)}` : "");
          }
          if (typeof m === "object" && m !== null) {
            try { return JSON.stringify(m); } catch { return String(m); }
          }
          return String(m);
        };
        const caerATexto = async (motivo: unknown) => {
          if (finalizadoRef.current || avatarRef.current !== "conectando") return;
          avatarRef.current = "abandonado";
          if (vigilante) clearTimeout(vigilante);
          console.error("❌ Avatar no disponible, cayendo a texto:", motivo);
          bitacora("FALLA: " + describir(motivo));
          if (DIAGNOSTICO_AVATAR) {
            // DIAGNÓSTICO: NO se cae a texto — el error crudo queda en pantalla para verlo en la demo.
            setErrorAvatar(describir(motivo));
            return;
          }
          anamRef.current = null;
          try {
            await anam.stopStreaming?.();
          } catch {}
          microfonoRef.current?.getTracks().forEach((t) => t.stop());
          microfonoRef.current = null;
          await entrarTexto(null);
        };

        anam.addListener(AnamEvent.MESSAGE_HISTORY_UPDATED, (historial: { role: string; content: string }[]) => {
          const nuevo = (historial ?? []).map((m) => ({
            rol: (m.role === "persona" ? "assistant" : "user") as Msg["rol"],
            texto: m.content,
          }));
          // un historial más corto (p. ej. vacío al cerrar el stream) nunca pisa lo ya capturado
          if (nuevo.length < transcriptRef.current.length) return;
          transcriptRef.current = nuevo;
          setMensajes(transcriptRef.current.slice(-4));
          sincronizar();
          // Cierre automático (Fase 4, Punto 4 — fallback "marcador"): la entrevistadora se despide con una frase
          // fija; el servidor verifica que esté en el transcript antes de aceptar el cierre.
          const ultimo = transcriptRef.current[transcriptRef.current.length - 1];
          if (ultimo?.rol === "assistant" && ultimo.texto.toLowerCase().includes(DESPEDIDA)) {
            if (silencioRef.current.timer) clearTimeout(silencioRef.current.timer);
            // deja que termine de decir la despedida antes de cortar el stream
            setTimeout(() => cerrar("marcador", true), 6000);
          }
        });
        // Silencio: se reprograma cada vez que la persona termina de hablar; se pausa mientras habla.
        anam.addListener(AnamEvent.USER_SPEECH_STARTED, () => {
          silencioRef.current.hablando = true;
          if (silencioRef.current.timer) clearTimeout(silencioRef.current.timer);
        });
        anam.addListener(AnamEvent.USER_SPEECH_ENDED, () => {
          silencioRef.current.hablando = false;
          silencioRef.current.avisos = 0;
          programarAvisoSilencio();
        });
        // Cualquiera de las dos señales confirma que el avatar ya está en la sala (SDK 4.x manda ambas;
        // con una basta para que el vigilante deje de contar).
        anam.addListener(AnamEvent.SESSION_READY, () => avatarListo("SESSION_READY"));
        anam.addListener(AnamEvent.VIDEO_PLAY_STARTED, () => avatarListo("VIDEO_PLAY_STARTED"));
        anam.addListener(AnamEvent.SERVER_WARNING, (msg: unknown) => {
          console.warn("⚠️ Anam:", msg);
          bitacora("SERVER_WARNING: " + describir(msg));
        });
        for (const ev of ["CONNECTION_ESTABLISHED", "DATA_CHANNEL_OPEN", "INPUT_AUDIO_STREAM_STARTED", "VIDEO_STREAM_STARTED", "AUDIO_STREAM_STARTED", "MIC_PERMISSION_PENDING", "MIC_PERMISSION_GRANTED", "MIC_PERMISSION_DENIED", "SESSION_READY", "VIDEO_PLAY_STARTED"]) {
          anam.addListener(ev, () => bitacora(ev));
        }
        // Respaldo: si Anam corta la conexión (timeout de ANAM_MAX_SESION_SEG, falla de red) sin
        // que hubiera cierre automático ni botón, igual se cierra — el servidor lo registra como
        // "desconexion" y, si casi no hubo turnos, la deja como interrumpida para que RH la reabra.
        anam.addListener(AnamEvent.CONNECTION_CLOSED, (codigo?: string, razon?: string) => {
          bitacora(`CONNECTION_CLOSED code=${codigo ?? "?"} reason=${razon ?? ""} estado=${avatarRef.current}`);
          // lo cerramos nosotros al caer a texto: no es una desconexión del candidato
          if (avatarRef.current === "abandonado") return;
          // Si la conexión se cierra ANTES de que el avatar estuviera listo, fue una falla del
          // avatar (no del candidato): se cae a texto en vez de marcar la entrevista interrumpida.
          if (avatarRef.current === "conectando") {
            return void caerATexto(`conexión cerrada antes de iniciar (${codigo ?? "?"}${razon ? ": " + razon : ""})`);
          }
          cerrar("desconexion", true);
        });
        setModo("avatar");
        setFase("sala");
        bitacora("createClient OK, esperando <video> y stream…");
        vigilante = setTimeout(() => caerATexto(`sin SESSION_READY ni video en ${ESPERA_AVATAR_SEG} s`), ESPERA_AVATAR_SEG * 1000);
        void (async () => {
          if (!(await esperarElemento("avatar-video"))) return caerATexto("no se montó el elemento de video");
          // Un segundo intento cubre fallas transitorias al arrancar la sesión (red, 5xx de Anam);
          // "Already streaming" significa que el primer intento sí conectó y solo falló después.
          for (let intento = 1; intento <= 2; intento++) {
            if (avatarRef.current !== "conectando") return;
            try {
              await client.streamToVideoElement("avatar-video", microfono ?? undefined);
              return;
            } catch (err) {
              console.error(`❌ streamToVideoElement falló (intento ${intento}):`, err);
              bitacora(`streamToVideoElement intento ${intento}: ` + describir(err));
              if (intento === 2 || /already streaming/i.test(String((err as Error)?.message ?? err))) return caerATexto(err);
              await new Promise((r) => setTimeout(r, 1500));
            }
          }
        })();
        return;
      } catch (err) {
        // si el avatar falla en el navegador, seguimos por texto (sesión de texto forzada)
        console.error("❌ Error inicializando Anam:", err);
        const e = err as Error;
        bitacora(`init SDK falló: ${e?.name ?? ""} ${e?.message ?? String(err)}`);
        microfonoRef.current?.getTracks().forEach((t) => t.stop());
        microfonoRef.current = null;
        if (DIAGNOSTICO_AVATAR) {
          setErrorAvatar(`Error inicializando el SDK de Anam: ${e?.name ?? ""} ${e?.message ?? String(err)}`);
          setModo("avatar");
          return setFase("sala");
        }
        return entrarTexto(null);
      }
    }

    if (DIAGNOSTICO_AVATAR && s.modo === "avatar" && !s.session_token) {
      setErrorAvatar("El backend dijo modo=avatar pero session_token llegó VACÍO.");
    }
    await entrarTexto(s);
  }, [token, cerrar, programarAvisoSilencio, rechazoSesion, entrarTexto, bitacora, sincronizar]);

  const enviar = useCallback(async () => {
    const t = texto.trim();
    if (!t || pensando) return;
    setTexto("");
    setMensajes((m) => [...m, { rol: "user", texto: t }]);
    setPensando(true);
    const turno = await turnoEntrevista(token, t);
    setPensando(false);
    if (!turno.ok) {
      setMensajes((m) => [...m, { rol: "assistant", texto: `No pude procesar tu respuesta: ${turno.error}` }]);
      return;
    }
    const r = turno.data;
    setMensajes((m) => [...m, { rol: "assistant", texto: r.respuesta }]);
    // Cierre automático en modo texto: la IA marcó `terminada` y se despidió; el servidor verifica.
    if (r.terminada) await cerrar("texto", false);
  }, [texto, pensando, token, cerrar]);

  const terminar = useCallback(() => cerrar("manual", modo === "avatar"), [cerrar, modo]);

  const enSala = fase === "sala" || fase === "finalizando";
  // Tótem + sala en video: pantalla completa inmersiva (sin header ni márgenes).
  // 2026-10-09: «Llamada Red Human» = la misma sesión de voz, sin video (la sala se muestra como llamada)
  const esLlamada = info?.tipo === "llamada";
  const salaTotem = totem && enSala && modo === "avatar" && !esLlamada;

  return (
    <main className={cn("sala-publica min-h-svh bg-bg", totem && "totem text-lg", salaTotem && "fixed inset-0 overflow-hidden")}>
      <link rel="preconnect" href="https://api.anam.ai" />
      {!salaTotem && (
        <header className="border-b border-border-soft">
          <div className={cn("mx-auto flex max-w-3xl items-center justify-between px-5 py-4", totem && "max-w-none px-10 py-8")}>
            <Logo size={totem ? "lg" : "md"} />
            <ThemeToggle />
          </div>
        </header>
      )}

      <div className={cn("mx-auto max-w-3xl px-5 py-8 sm:py-10", totem && !salaTotem && "max-w-4xl px-10 py-14", salaTotem && "h-full max-w-none p-0")}>
        {DIAGNOSTICO_AVATAR && seguro === false && (
          <div className="mb-6 rounded-xl border-4 border-red-600 bg-red-600 p-6 text-center text-white">
            <p className="text-2xl font-black tracking-wide sm:text-3xl">HTTPS REQUERIDO PARA EL AVATAR</p>
            <p className="mt-2 text-sm font-semibold">
              Esta página se abrió por HTTP (contexto no seguro). El navegador bloquea el micrófono y WebRTC, así que el
              video de Red Human no puede iniciar. Abre la misma liga con <b>https://</b>.
            </p>
            <p className="mt-1 break-all font-mono text-xs opacity-90">{typeof window !== "undefined" ? window.location.href : ""}</p>
          </div>
        )}
        {DIAGNOSTICO_AVATAR && fase !== "cargando" && (
          <div className="mb-4 rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 font-mono text-[11px] leading-snug text-ink">
            <p className="font-bold">DIAGNÓSTICO AVATAR (temporal)</p>
            <p>secureContext: {seguro === null ? "?" : String(seguro)} · protocolo: {typeof window !== "undefined" ? window.location.protocol : "?"} · avatar_disponible (backend): {info ? String(info.avatar_disponible) : "?"}</p>
            <p>modo: {debug.modo ?? "—"} · session_token: {debug.token ?? "—"}{debug.motivo ? ` · motivo: ${debug.motivo}` : ""}</p>
            {debug.eventos.length > 0 && (
              <ul className="mt-1 max-h-40 overflow-y-auto border-t border-amber-500/40 pt-1">
                {debug.eventos.map((l, i) => (
                  <li key={i} className="break-all">{l}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {DIAGNOSTICO_AVATAR && errorAvatar && (
          <div className="mb-4 rounded-xl border-2 border-red-600 bg-red-600/15 p-4 text-red-700 dark:text-red-300">
            <p className="text-base font-black">❌ ERROR DEL AVATAR (sin fallback a texto — modo diagnóstico)</p>
            <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-xs">{errorAvatar}</pre>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" onClick={() => { setErrorAvatar(""); void empezar(); }}>Reintentar video</Button>
              <Button size="sm" variant="secondary" onClick={() => { setErrorAvatar(""); avatarRef.current = "abandonado"; anamRef.current = null; void entrarTexto(null); }}>Continuar por chat</Button>
            </div>
          </div>
        )}
        {fase === "cargando" && (
          <div className="grid place-items-center py-24 text-ink-3">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        )}

        {fase === "no_disponible" && (
          <Card className="p-8 text-center">
            <h1 className="font-display text-xl font-bold">Liga no disponible</h1>
            <p className="mt-2 text-sm text-ink-2">
              Esta entrevista no existe o ya no está activa. Verifica la liga que recibiste o contacta al equipo de RH.
            </p>
          </Card>
        )}

        {fase === "cerrada" && (
          <Card className="p-8 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-warn/10">
              <ShieldCheck className="h-7 w-7 text-warn" />
            </span>
            <h1 className="font-display mt-4 text-xl font-bold">Esta entrevista ya está cerrada</h1>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">{errorTexto || MENSAJE_CERRADA}</p>
          </Card>
        )}

        {fase === "error" && (
          <Card className="p-8 text-center">
            <h1 className="font-display text-xl font-bold">No pudimos iniciar tu entrevista</h1>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">{errorTexto}</p>
            <Button className="mt-5 totem:min-h-16 totem:px-8 totem:text-xl" onClick={() => setFase("consentimiento")}>
              Intentar de nuevo
            </Button>
          </Card>
        )}

        {fase === "microfono" && (
          <Card className="p-8 text-center">
            <h1 className="font-display text-xl font-bold">Necesitamos tu micrófono</h1>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">{errorTexto || MENSAJE_MICROFONO}</p>
            <div className="mt-5 flex flex-wrap justify-center gap-3">
              <Button onClick={empezar} className="totem:min-h-16 totem:px-8 totem:text-xl">
                <Video className="h-4 w-4" />
                Reintentar en video
              </Button>
              <Button
                variant="secondary"
                className="totem:min-h-16 totem:px-8 totem:text-xl"
                onClick={() => {
                  setFase("conectando");
                  void entrarTexto(null);
                }}
              >
                <MessageCircle className="h-4 w-4" />
                Continuar por chat
              </Button>
            </div>
          </Card>
        )}

        {fase === "consentimiento" && info && (
          <>
            <div className="text-center">
              <Badge tone="good" dot>
                {esLlamada ? "Llamada" : "Entrevista"} · {info.empresa}
              </Badge>
              <h1 className={cn("font-display mt-3 text-2xl font-bold sm:text-3xl", totem && "text-5xl leading-tight sm:text-5xl")}>
                Hola {info.candidato.split(" ")[0]}, tu {esLlamada ? "llamada" : "entrevista"} para {info.puesto}
              </h1>
              <p className={cn("mx-auto mt-2 max-w-xl leading-relaxed text-ink-2", totem ? "max-w-3xl text-2xl" : "text-sm")}>
                Conversarás con <b className="text-ink">Red Human</b>, nuestra entrevistadora
                {esLlamada ? (info.avatar_disponible ? " por voz (sin video)" : " por chat") : info.avatar_disponible ? " en video" : " por chat"}.
                Dura alrededor de 10 minutos y puedes hacerla desde tu celular o computadora.
              </p>
            </div>

            <Card className="mt-6 p-6">
              <div className="flex items-start gap-3">
                <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-human" />
                <div className={cn("leading-relaxed text-ink-2", totem ? "text-xl" : "text-sm")}>
                  <p className="font-semibold text-ink">Antes de empezar, es importante que sepas:</p>
                  <ul className="mt-2 list-disc space-y-1.5 pl-4">
                    <li>La entrevista la conduce una <b>inteligencia artificial</b> de Red Human, no una persona.</li>
                    <li>La conversación se <b>graba y transcribe</b> para que el equipo de RH la revise.</li>
                    <li>
                      La IA solo genera una recomendación: <b>la decisión final siempre la toma una persona</b> del
                      equipo de RH.
                    </li>
                    <li>No se te pedirán datos sensibles (salud, religión, estado civil).</li>
                  </ul>
                </div>
              </div>

              <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-xl border border-border-soft p-4 transition hover:border-brand/40">
                <input
                  type="checkbox"
                  checked={acepto}
                  onChange={(e) => setAcepto(e.target.checked)}
                  className={cn("mt-0.5 accent-[var(--brand,#ee4444)]", totem ? "h-8 w-8" : "h-4 w-4")}
                />
                <span className={cn("text-ink-2", totem ? "text-xl leading-relaxed" : "text-sm")}>
                  Acepto participar en esta entrevista con IA y autorizo la grabación y el tratamiento de mis
                  respuestas para este proceso de selección.
                </span>
              </label>

              {totem && perifericos?.soportado && perifericos.microfonos === 0 && (
                <p className="mt-4 flex items-center gap-3 rounded-2xl border border-warn/40 bg-warn-soft px-5 py-4 text-xl font-semibold text-warn">
                  <MicOff className="h-7 w-7 shrink-0" /> No se detecta ningún micrófono. Conecta el micrófono USB y espera un momento; esta pantalla se actualiza sola.
                </p>
              )}
              <Button className={cn("mt-5 w-full", totem && "min-h-20 rounded-3xl text-2xl")} size={totem ? "lg" : "md"} disabled={!acepto} onClick={empezar}>
                {esLlamada && info.avatar_disponible ? <Phone className={totem ? "h-7 w-7" : "h-4 w-4"} />
                  : info.avatar_disponible ? <Video className={totem ? "h-7 w-7" : "h-4 w-4"} /> : <MessageCircle className={totem ? "h-7 w-7" : "h-4 w-4"} />}
                {esLlamada ? "Comenzar llamada" : "Comenzar entrevista"}
              </Button>
            </Card>
          </>
        )}

        {fase === "conectando" && (
          <div className="grid place-items-center gap-3 py-24 text-ink-2">
            <Loader2 className="h-6 w-6 animate-spin text-brand" />
            <p className="text-sm">Preparando tu entrevista…</p>
          </div>
        )}

        {salaTotem && (
          <div className="flex h-full flex-col bg-[#0f0f11] text-white">
            {/* Video: llena todo el espacio superior/central; object-cover recorta sin deformar */}
            <div className="relative min-h-0 flex-1">
              <video id="avatar-video" autoPlay playsInline muted={!sonido} className="absolute inset-0 h-full w-full object-cover" />
              <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/60 to-transparent px-8 pb-16 pt-8">
                <Logo onDark size="lg" />
                <span className="flex items-center gap-2 rounded-full bg-black/50 px-4 py-2 font-mono text-base text-white/90 backdrop-blur">
                  <Sparkles className="h-4 w-4" /> Red Human · en video
                </span>
              </div>
              {/* Subtítulos grandes de los últimos dos turnos */}
              <div className="pointer-events-none absolute inset-x-0 bottom-0 space-y-3 bg-gradient-to-t from-black/85 via-black/50 to-transparent px-8 pb-8 pt-24">
                {mensajes.slice(-2).map((m, i) => (
                  <p key={i} className={cn("text-2xl leading-snug", m.rol === "assistant" ? "text-white" : "text-white/80")}>
                    <b className={m.rol === "assistant" ? "text-brand" : "text-white/60"}>{m.rol === "assistant" ? "Red Human: " : "Tú: "}</b>
                    {m.texto}
                  </p>
                ))}
                {!micActivo && (
                  <p className="flex items-center gap-2 text-xl font-semibold text-warn">
                    <MicOff className="h-6 w-6" /> Micrófono silenciado — toca «Hablar» para responder
                  </p>
                )}
              </div>
            </div>

            {/* Controles táctiles enormes */}
            <div className="grid grid-cols-3 gap-5 border-t border-white/10 bg-[#151517] px-8 pb-[max(2.5rem,env(safe-area-inset-bottom))] pt-7">
              <button
                type="button"
                onClick={alternarMic}
                disabled={fase === "finalizando"}
                aria-pressed={micActivo}
                className={cn(
                  "flex h-40 flex-col items-center justify-center gap-3 rounded-3xl text-2xl font-bold transition active:scale-95 disabled:opacity-50",
                  micActivo ? "bg-white/10 text-white ring-2 ring-white/20" : "bg-good text-white shadow-[0_0_0_10px_rgba(34,197,94,0.25)] animate-pulse",
                )}
              >
                {micActivo ? <MicOff className="h-14 w-14" /> : <Mic className="h-14 w-14" />}
                {micActivo ? "Silenciar micrófono" : "Hablar"}
              </button>
              <button
                type="button"
                onClick={alternarSonido}
                disabled={fase === "finalizando"}
                aria-pressed={sonido}
                className="flex h-40 flex-col items-center justify-center gap-3 rounded-3xl bg-white/10 text-2xl font-bold text-white ring-2 ring-white/20 transition active:scale-95 disabled:opacity-50"
              >
                {sonido ? <Volume2 className="h-14 w-14" /> : <VolumeX className="h-14 w-14" />}
                {sonido ? "Silenciar sonido" : "Activar sonido"}
              </button>
              <button
                type="button"
                onClick={terminar}
                disabled={fase === "finalizando"}
                className="flex h-40 flex-col items-center justify-center gap-3 rounded-3xl bg-brand text-2xl font-bold text-white shadow-lg transition active:scale-95 disabled:opacity-50"
              >
                {fase === "finalizando" ? <Loader2 className="h-14 w-14 animate-spin" /> : <Phone className="h-14 w-14 rotate-[135deg]" />}
                {fase === "finalizando" ? "Cerrando…" : "Terminar"}
              </button>
              <p className="col-span-3 flex items-center justify-center gap-2 text-base text-white/50">
                <ShieldCheck className="h-5 w-5" /> Conversación grabada con tu consentimiento · la decisión final la toma una persona de RH
              </p>
            </div>
          </div>
        )}

        {enSala && !salaTotem && (
          <Card className="overflow-hidden">
            {modo === "avatar" && esLlamada ? (
              <div className="relative grid min-h-[20rem] place-items-center bg-[#151517] px-6 py-10 text-center">
                {/* el audio sale del mismo <video> de la sesión; en llamada no se muestra la imagen */}
                <video id="avatar-video" autoPlay playsInline className="pointer-events-none absolute h-px w-px opacity-0" aria-hidden />
                <div className="flex flex-col items-center gap-4">
                  <span className="grid h-20 w-20 animate-pulse place-items-center rounded-full bg-brand/20 text-brand">
                    <Phone className="h-9 w-9" />
                  </span>
                  <p className="font-display text-lg font-bold text-white">Llamada con Red Human</p>
                  <div className="max-w-md space-y-1">
                    {mensajes.slice(-2).map((m, i) => (
                      <p key={i} className="text-[13px] leading-snug text-white/80">
                        <b>{m.rol === "assistant" ? "Red Human: " : "Tú: "}</b>
                        {m.texto}
                      </p>
                    ))}
                  </div>
                </div>
              </div>
            ) : modo === "avatar" ? (
              <div className="relative aspect-video bg-[#151517]">
                <video id="avatar-video" autoPlay playsInline className="h-full w-full object-cover" />
                <span className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-black/50 px-2.5 py-1 font-mono text-[11px] text-white/90 backdrop-blur">
                  <Sparkles className="h-3 w-3" /> Red Human · en video
                </span>
                <div className="absolute inset-x-0 bottom-0 space-y-1 bg-gradient-to-t from-black/70 to-transparent p-4 pt-10">
                  {mensajes.slice(-2).map((m, i) => (
                    <p key={i} className="text-[13px] leading-snug text-white/90">
                      <b>{m.rol === "assistant" ? "Red Human: " : "Tú: "}</b>
                      {m.texto}
                    </p>
                  ))}
                </div>
              </div>
            ) : (
              <div ref={chatRef} className="h-[26rem] space-y-3 overflow-y-auto p-5 totem:h-[60svh]">
                {mensajes.map((m, i) => (
                  <div key={i} className={cn("flex", m.rol === "user" ? "justify-end" : "justify-start")}>
                    <div
                      className={cn(
                        "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed totem:text-xl",
                        m.rol === "user" ? "bg-brand text-white" : "bg-surface-2 text-ink",
                      )}
                    >
                      {m.rol === "assistant" && (
                        <span className="mb-0.5 flex items-center gap-1 font-mono text-[10px] font-semibold text-brand">
                          <Sparkles className="h-3 w-3" /> RED HUMAN
                        </span>
                      )}
                      {m.texto}
                    </div>
                  </div>
                ))}
                {pensando && (
                  <div className="flex items-center gap-2 text-ink-3">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" />
                    <span className="text-xs italic">Red Human está escribiendo…</span>
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center gap-2 border-t border-border-faint p-4">
              {modo === "texto" && (
                <>
                  <input
                    value={texto}
                    onChange={(e) => setTexto(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && enviar()}
                    placeholder="Escribe tu respuesta…"
                    disabled={fase === "finalizando"}
                    className="h-11 flex-1 rounded-xl border border-border-soft bg-surface px-4 text-sm outline-none transition focus:border-brand totem:min-h-16 totem:text-xl"
                  />
                  <Button size="sm" onClick={enviar} disabled={!texto.trim() || fase === "finalizando"} className="totem:min-h-16 totem:min-w-20 totem:text-xl">
                    <Send className="h-4 w-4 totem:h-7 totem:w-7" />
                  </Button>
                </>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={terminar}
                disabled={fase === "finalizando"}
                className={cn(modo === "avatar" && "ml-auto", "totem:min-h-16 totem:px-8 totem:text-xl")}
              >
                {fase === "finalizando" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Phone className="h-4 w-4 rotate-[135deg]" />
                )}
                Terminar entrevista
              </Button>
            </div>

            <div className="flex items-center gap-2.5 border-t border-border-faint bg-human-soft/40 px-5 py-2.5">
              <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-human" />
              <p className="text-[12px] text-ink-3">
                Conversación grabada con tu consentimiento. La decisión final la toma una persona de RH.
              </p>
            </div>
          </Card>
        )}

        {fase === "interrumpida" && (
          <Card className="p-8 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-warn/10">
              <Phone className="h-7 w-7 rotate-[135deg] text-warn" />
            </span>
            <h1 className="font-display mt-4 text-2xl font-bold">La entrevista se interrumpió</h1>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-ink-2">
              {info?.motivo === "sin_respuestas"
                ? "No recibimos tus respuestas."
                : info?.estado === "parcial"
                  ? "La entrevista quedó incompleta."
                  : "Se perdió la conexión antes de que pudiéramos platicar."}{" "}
              No te preocupes: el equipo de RH{info ? ` de ${info.empresa}` : ""} puede reabrir esta misma liga para que la retomes.
            </p>
          </Card>
        )}

        {fase === "fin" && (
          <Card className="p-8 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-good/10">
              <CheckCircle2 className="h-7 w-7 text-good" />
            </span>
            <h1 className={cn("font-display mt-4 text-2xl font-bold", totem && "text-5xl")}>¡Gracias por tu entrevista!</h1>
            <p className={cn("mx-auto mt-2 max-w-md leading-relaxed text-ink-2", totem ? "max-w-2xl text-2xl" : "text-sm")}>
              Tus respuestas quedaron registradas. El equipo de RH{info ? ` de ${info.empresa}` : ""} las revisará y te
              contactará muy pronto con el siguiente paso. 😊
            </p>
          </Card>
        )}
      </div>
    </main>
  );
}
