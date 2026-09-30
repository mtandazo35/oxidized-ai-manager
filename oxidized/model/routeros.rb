# Modelo RouterOS propio. Copia del de Oxidized 0.37.0 con UN cambio, marcado
# abajo con «CAMBIO».
#
# OJO AL MANTENERLO: un modelo en `~/.config/oxidized/model/` **reemplaza** al
# que trae Oxidized, no lo extiende (ver `Manager#loader`). Así que al subir la
# versión de Oxidized hay que comparar este archivo contra el nuevo
# `lib/oxidized/model/routeros.rb` de upstream y traer lo que haya cambiado, o
# nos quedamos sin las correcciones de modelo que publiquen.
#
# CAMBIO — no reventar el nodo cuando no se puede leer la versión.
# El modelo original hace `/([0-9])/.match(version_line)[0]` sobre un valor que
# puede ser `nil`: un equipo que no responda a `/system package update print`
# como se espera se queda **sin ningún respaldo**, por un NoMethodError, por no
# haber podido leer su número de versión. Aquí la versión ilegible pasa a ser 0,
# que cae en la rama del `/export` a secas: un export válido en cualquier
# RouterOS, que es lo que hay que hacer cuando no se sabe con qué se habla.
#
# LO QUE **NO** HAY QUE CAMBIAR AQUÍ, y por qué (probado en producción):
# la elección entre `/export` y `/export show-sensitive` según la versión NO es
# un descuido de upstream, es la diferencia real entre RouterOS 6 y 7.
#   - RouterOS 7 oculta los datos sensibles por defecto y hay que pedir
#     `show-sensitive` para que salgan.
#   - RouterOS 6 los incluye en el `/export` normal, y **no acepta**
#     `show-sensitive`: contestó `expected end of command (line 1 column 9)` en
#     un CCR con 6.49.21, y ese texto de error se guardó como si fuera el
#     respaldo del equipo (219 KB de configuración pasaron a 7 KB de error).
# En las dos versiones hace falta que la cuenta de respaldo tenga la policy
# `sensitive`; sin ella el export sale censurado aunque el comando sea el
# correcto. Detalles en docs/ROUTEROS_RESPALDO_COMPLETO.md.

class RouterOS < Oxidized::Model
  using Refinements

  prompt /\[\w+@\S+(\s+\S+)*\]\s?>\s?$/
  comment "# "

  cmd :all do |cfg|
    cfg.gsub! /\x1B\[([0-9]{1,3}(;[0-9]{1,3})*)?[m|K]/, '' # strip ANSI colours
    if screenscrape
      cfg = cfg.cut_both
      cfg.gsub! /^\r+(.+)/, '\1'
      cfg.gsub! /([^\r]*)\r+$/, '\1'
    end
    cfg.lines.map { |line| line.rstrip }.join("\n") + "\n" # strip trailing whitespace
  end

  cmd '/system resource print' do |cfg|
    cfg = cfg.each_line.grep(/(version|factory-software|total-memory|cpu|cpu-count|total-hdd-space|architecture-name|board-name|platform):/).join
    comment cfg
  end

  cmd '/system package update print' do |cfg|
    # CAMBIO: `.to_s` sobre el resultado del grep, que puede ser nil, y versión
    # 0 cuando no hay ningún dígito que leer.
    version_line = cfg.each_line.grep(/installed-version:\s|current-version:\s/)[0].to_s
    @ros_version = version_line[/[0-9]/].to_i
    comment version_line
  end

  cmd '/system history print without-paging' do |cfg|
    comment cfg
  end

  cmd :significant_changes do |cfg|
    cfg.gsub(/^(#\s+installed-version: [^\n]+\n).*?^(?=# software id)/m, '\1')
  end

  post do
    logger.debug "Running /export for routeros version #{@ros_version}"
    run_cmd = if vars(:remove_secret)
                '/export hide-sensitive'
              elsif @ros_version >= 7
                '/export show-sensitive'
              else
                '/export'
              end
    cmd run_cmd do |cfg|
      cfg.gsub! /\\\r?\n\s+/, '' # strip new line
      cfg.gsub! "# inactive time\r\n", '' # Remove time based system comment
      cfg.gsub! /# received packet from \S+ bad format\r\n/, '' # Remove intermittent VRRP/CARP collision comment
      cfg.gsub! "# poe-out status: short_circuit\r\n", '' # Remove intermittent POE short_circuit comment
      cfg.gsub! "# Firmware upgraded successfully, please reboot for changes to take effect!\r\n", '' # Remove transient firmware upgrade comment
      cfg.gsub! /# \S+ not ready\r\n/, '' # Remove intermittent $interface not ready comment
      cfg.gsub! /# .+ please restart the device in order to apply the new setting\r\n/, '' # Remove intermittent restart needed comment. (e.g. for ipv6 settings)
      cfg = cfg.split("\n")
      cfg.reject! { |line| line[/^#\s\w{3}\/\d{2}\/\d{4}.*$/] } # Remove date time and 'by RouterOS' comment (v6)
      cfg.reject! { |line| line[/^#\s\d{4}-\d{2}-\d{2}.*$/] }   # Remove date time and 'by RouterOS' comment (v7)
      cfg.join("\n") + "\n"
    end
  end

  cfg :telnet do
    username /^Login:/
    password /^Password:/
  end

  cfg :telnet, :ssh do
    pre_logout 'quit'
  end

  cfg :ssh do
    exec true
  end
end
