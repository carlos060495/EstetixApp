/**
 * LO QUE SE BORRA A LOS 90 DÍAS Y LO QUE SE QUEDA (19-10-2026).
 *
 * Copia letra a letra de `TEXTO_DEL_BORRADO` en backend/app/core/simulacro.py,
 * donde cada tabla que sobrevive al borrado está nombrada en una frase. Lo
 * dicen el diálogo de cancelar y la política de privacidad; los correos del
 * plazo lo dicen desde el backend. Si alguien cambia uno sin el otro, o se
 * guarda una tabla más sin nombrarla, los tests se ponen rojos.
 */
export const LO_QUE_SE_BORRA =
  "Tus datos se conservan 90 días y después se borran: centros, clientas, citas, equipo, fichajes y tu cuenta. Queda constancia, con tu nombre y tu correo, de los cambios de plan y de pago, de las descargas de datos y de quién entró a darte soporte. Queda también, sin tu correo, lo que nuestro equipo hizo en tu cuenta desde el panel y, si llegaste por un comercial, tu alta y lo que se le paga por ella. Las facturas las guarda Stripe.";
