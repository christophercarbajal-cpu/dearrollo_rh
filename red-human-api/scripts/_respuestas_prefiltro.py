"""Ayuda de las verificaciones (Cambio 29): contesta la pregunta PENDIENTE del prefiltro operativo fijo con un dato
que cumple (escolaridad, experiencia, licencia, disponibilidad, edad, puesto, sueldo, colonia, traslado)."""

RESPUESTAS = {"escolaridad": "Terminé la licenciatura", "experiencia": "3 años", "licencia": "Tipo B",
              "disponibilidad": "Puedo todos los turnos", "requisito": "Sí, lo hice en mi trabajo anterior", "edad": "30",
              "interes": "Sí", "sueldo": "Sí", "zona": "Colonia Americana, Guadalajara", "puede_trasladarse": "Sí",
              "traslado": "40 minutos", "transporte": "En camión"}


def respuesta_pendiente(p, por_defecto: str = "Sí") -> str:
    from app.services import prefiltro_conversacional as pconv

    pid = pconv.estado(p).get("pendiente")
    c = next((x for x in pconv.criterios_de(p.vacante, p) if x["id"] == pid), None)
    return RESPUESTAS.get((c or {}).get("dato"), por_defecto)
