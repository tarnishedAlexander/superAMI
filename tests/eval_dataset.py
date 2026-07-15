"""Dataset del eval de retrieval/gate. Tres clases:
- directa: debe responderse one-shot; `esperado` = substring del nombre del trámite correcto.
- ambigua: legítimamente ambigua; el gate debe pedir aclaración.
- no_satisfacible: no existe en el dataset gob.bo; el gate debe gatearla (lejano), no inventar.
Curado a mano; las variantes generadas (tests/generar_variantes.py) se agregan acá tras revisión.
"""
import unicodedata


def normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFD", texto)
    return "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn").upper()


CASOS = [
    # --- directas (verificadas en el eval del demo, 2026-07-14) ---
    {"frase": "necesito el papel del carro", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "quiero los papeles de propiedad de mi auto", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "certificado de nacimiento", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "necesito el certificado de nacimiento de mi hija", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "registrar a mi hijo recién nacido", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "sacar el NIT para mi negocio", "clase": "directa", "esperado": "NIT"},
    {"frase": "cómo me inscribo en impuestos para poder facturar", "clase": "directa", "esperado": "NIT"},
    {"frase": "certificado de antecedentes penales", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "necesito mi certificado de antecedentes para un trabajo", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "quiero abrir mi empresa", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "registrar mi empresa para que sea legal", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "cobrar la renta dignidad", "clase": "directa", "esperado": "RENTA DIGNIDAD"},
    {"frase": "mi abuelita quiere cobrar su bono de vejez", "clase": "directa", "esperado": "RENTA DIGNIDAD"},
    {"frase": "quiero poner una farmacia", "clase": "directa", "esperado": "FARMACIA"},
    {"frase": "qué necesito para abrir una farmacia en mi barrio", "clase": "directa", "esperado": "FARMACIA"},
    {"frase": "carnet de discapacidad", "clase": "directa", "esperado": "DISCAPACIDAD"},
    {"frase": "cómo saco el carnet de discapacidad para mi hermano", "clase": "directa", "esperado": "DISCAPACIDAD"},
    {"frase": "título de bachiller", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "perdí mi título de bachiller, necesito otro", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "apostillar mis documentos para salir del país", "clase": "directa", "esperado": "APOSTILLA"},
    {"frase": "legalizar documentos para usarlos en el extranjero", "clase": "directa", "esperado": "APOSTILLA"},
    {"frase": "certificado de defunción", "clase": "directa", "esperado": "DEFUNCION"},
    {"frase": "falleció mi papá y necesito el certificado", "clase": "directa", "esperado": "DEFUNCION"},
    # --- directas plausibles (verificadas contra la DB real 2026-07-14: 0 faltantes) ---
    {"frase": "registrar la marca de mi producto", "clase": "directa", "esperado": "MARCA"},
    {"frase": "registro sanitario para vender alimentos", "clase": "directa", "esperado": "SANITARIO"},
    {"frase": "quiero exportar mis productos, qué necesito", "clase": "directa", "esperado": "EXPORTA"},
    {"frase": "personería jurídica para nuestra asociación", "clase": "directa", "esperado": "PERSONALIDAD JURIDICA"},
    {"frase": "duplicado de la libreta de servicio militar", "clase": "directa", "esperado": "LIBRETA"},
    {"frase": "inscribirme al SUS para atenderme en el hospital", "clase": "directa", "esperado": "SUS"},
    {"frase": "quiero tramitar mi jubilación", "clase": "directa", "esperado": "JUBILACION"},
    # --- variantes generadas y curadas (2026-07-14, tests/generar_variantes.py) ---
    # Curación: se descartaron duplicados temáticos (ej. 3+ variantes casi iguales de un
    # mismo trámite), una frase con fraseo artificial ("vivir con dignidad") y una que
    # podía mapear a un trámite distinto (permiso de venta callejera != registro sanitario).
    {"frase": "Necesito sacar el papeleo del auto, ¿dónde lo hago?", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "Me falta el documento del vehículo, ¿cómo lo tramito?", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "Quiero saber cómo sacar la partida de mi vehículo.", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "Necesito los trámites para obtener el título de propiedad de mi carro.", "clase": "directa", "esperado": "VEHICULO"},
    {"frase": "Mi partida de nacimiento, ¿cómo la saco?", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "Necesito mi acta de nacido, ¿dónde la tramito?", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "Necesito sacar el parto de mi ch'iquita, ¿cómo lo hago?", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "Mi hija necesita su certificado de nacimiento, ¿qué debo hacer?", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "Quiero hacer el registro de mi bebé que acaba de nacer, ¿qué debo hacer?", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "Necesito saber cómo inscribir a mi hijo en el registro civil, ¿me podés ayudar?", "clase": "directa", "esperado": "NACIMIENTO"},
    {"frase": "Sacar el NIT para mi tienda de ropa, ¿cómo hago?", "clase": "directa", "esperado": "NIT"},
    {"frase": "Necesito sacar el NIT para mi negocio de comida, ¿qué trámites debo hacer?", "clase": "directa", "esperado": "NIT"},
    {"frase": "¿Cómo hago para inscribirme en el SIRE para poder hacer facturas?", "clase": "directa", "esperado": "NIT"},
    {"frase": "¿Dónde me inscribo para poder empezar a emitir facturas?", "clase": "directa", "esperado": "NIT"},
    {"frase": "Necesito sacar el certificado de que no tengo antecedentes penales, ¿cómo hago?", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "Quiero pedir mi certificado de antecedentes, ¿dónde debo ir?", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "Necesito sacar mi certificado de antecedentes para un trabajo nuevo que estoy por empezar.", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "Quiero saber cómo puedo obtener mi certificado de antecedentes, lo necesito urgentemente para un empleo.", "clase": "directa", "esperado": "ANTECEDENTES"},
    {"frase": "Estoy pensando en montar mi propio negocio, ¿cómo hago para empezar?", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "Quiero ser mi propio jefe, ¿qué trámites necesito hacer para abrir mi empresa?", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "Necesito hacer el trámite para que mi negocio quede registrado y no tenga problemas con la ley.", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "Quiero hacer la inscripción de mi empresa para que esté todo en regla y no me multen.", "clase": "directa", "esperado": "EMPRESA"},
    {"frase": "cobrar la renta de la dignidad que me corresponde", "clase": "directa", "esperado": "RENTA DIGNIDAD"},
    {"frase": "Mi abuela está esperando el bono de vejez, ¿cuándo lo van a pagar?", "clase": "directa", "esperado": "RENTA DIGNIDAD"},
    {"frase": "Estoy pensando en abrir una farmacia, ¿qué trámites tengo que hacer?", "clase": "directa", "esperado": "FARMACIA"},
    {"frase": "Quiero montar una farmacia, ¿qué papeles necesito?", "clase": "directa", "esperado": "FARMACIA"},
    {"frase": "¿Cómo hago para sacar el carnet de discapacidad para mi hermano que tiene problemas de movilidad?", "clase": "directa", "esperado": "DISCAPACIDAD"},
    {"frase": "Quiero saber cómo obtener el carnet de discapacidad para mi tío que tiene una discapacidad visual.", "clase": "directa", "esperado": "DISCAPACIDAD"},
    {"frase": "Mi hermano tiene una discapacidad y no sé por dónde empezar para sacarle el carnet, ¿me podés ayudar?", "clase": "directa", "esperado": "DISCAPACIDAD"},
    {"frase": "Mi título de bachillerato", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "El título que me dieron cuando terminé el colegio", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "Se me perdió el título de bachiller, ¿cómo hago para obtener otro duplicado?", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "Me robaron el título de bachiller, ¿qué debo hacer para conseguir uno nuevo?", "clase": "directa", "esperado": "BACHILLER"},
    {"frase": "Necesito hacer la apostilla para mis papeles para viajar afuera.", "clase": "directa", "esperado": "APOSTILLA"},
    {"frase": "Quiero saber cómo hacer para que me sellen los documentos para viajar al exterior.", "clase": "directa", "esperado": "APOSTILLA"},
    {"frase": "Necesito hacer la legalización de mis documentos para que sean válidos en otro país.", "clase": "directa", "esperado": "APOSTILLA"},
    {"frase": "El papelito de cuando alguien se muere", "clase": "directa", "esperado": "DEFUNCION"},
    {"frase": "El certificado de cuando falleció mi abuelo", "clase": "directa", "esperado": "DEFUNCION"},
    {"frase": "Mi viejo falleció y necesito el papeleo para el certificado de defunción", "clase": "directa", "esperado": "DEFUNCION"},
    {"frase": "Acabo de perder a mi papá y necesito saber cómo sacar el certificado de fallecimiento", "clase": "directa", "esperado": "DEFUNCION"},
    {"frase": "Quiero hacer el trámite para registrar la marca de mi negocio, ¿cómo hago?", "clase": "directa", "esperado": "MARCA"},
    {"frase": "Necesito saber cómo registrar la marca de mi producto para evitar que me la copien.", "clase": "directa", "esperado": "MARCA"},
    {"frase": "Necesito hacer el trámite del registro sanitario para mi negocio de comida.", "clase": "directa", "esperado": "SANITARIO"},
    {"frase": "Necesito saber qué papeles tengo que hacer para vender mis cosas en el exterior.", "clase": "directa", "esperado": "EXPORTA"},
    {"frase": "Quiero empezar a exportar mis productos, ¿qué trámites tengo que hacer y dónde?", "clase": "directa", "esperado": "EXPORTA"},
    {"frase": "Queremos saber cómo sacar la personería jurídica para nuestra asociación que estamos armando.", "clase": "directa", "esperado": "PERSONALIDAD JURIDICA"},
    {"frase": "Necesitamos tramitar la personería jurídica para nuestra asociación que ya estamos funcionando de hecho.", "clase": "directa", "esperado": "PERSONALIDAD JURIDICA"},
    # --- ambiguas legítimas (el gate debe preguntar) ---
    {"frase": "necesito un certificado", "clase": "ambigua", "esperado": None},
    {"frase": "quiero sacar un documento", "clase": "ambigua", "esperado": None},
    {"frase": "trámites para mi negocio", "clase": "ambigua", "esperado": None},
    {"frase": "necesito registrar una propiedad", "clase": "ambigua", "esperado": None},
    {"frase": "papeles para viajar", "clase": "ambigua", "esperado": None},
    {"frase": "necesito un certificado para el banco", "clase": "ambigua", "esperado": None},
    {"frase": "quiero registrar a mi familia", "clase": "ambigua", "esperado": None},
    {"frase": "un permiso para vender en la calle", "clase": "ambigua", "esperado": None},
    # --- no satisfacibles (controles negativos verificados: no existen en gob.bo) ---
    {"frase": "quiero sacar mi carnet", "clase": "no_satisfacible", "esperado": None},
    {"frase": "renovar mi carnet de identidad", "clase": "no_satisfacible", "esperado": None},
    {"frase": "mi cédula está vencida, dónde la renuevo", "clase": "no_satisfacible", "esperado": None},
    {"frase": "sacar pasaporte", "clase": "no_satisfacible", "esperado": None},
    {"frase": "cuánto cuesta el pasaporte y dónde lo saco", "clase": "no_satisfacible", "esperado": None},
    {"frase": "licencia de conducir por primera vez", "clase": "no_satisfacible", "esperado": None},
    {"frase": "renovar mi brevet", "clase": "no_satisfacible", "esperado": None},
    {"frase": "quiero casarme, qué necesito", "clase": "no_satisfacible", "esperado": None},
    {"frase": "bono Juana Azurduy", "clase": "no_satisfacible", "esperado": None},
    {"frase": "certificado de soltería", "clase": "no_satisfacible", "esperado": None},
    {"frase": "pagar mis multas de tránsito", "clase": "no_satisfacible", "esperado": None},
    {"frase": "sacar el SOAT de mi auto", "clase": "no_satisfacible", "esperado": None},
]
