# Modelo RouterOS propio. Copia del modelo de Oxidized 0.37.0 con dos cambios,
# marcados abajo con «CAMBIO».
#
# OJO AL MANTENERLO: un modelo en `~/.config/oxidized/model/` **reemplaza** al
# que trae Oxidized, no lo extiende (ver `Manager#loader`). Así que al subir la
# versión de Oxidized hay que comparar este archivo contra el nuevo
# `lib/oxidized/model/routeros.rb` de upstream y traer lo que haya cambiado, o
# nos quedamos sin las correcciones de modelo que publiquen.
#
# CAMBIO 1 — `/export show-sensitive` también en RouterOS 6.
# Desde RouterOS 6.43 `/export` oculta los secretos por defecto y hay que pedir
# `show-sensitive` para que salgan. El modelo de upstream solo lo pide cuando
# detecta versión 7 o superior, así que en toda la flota v6 los respaldos salían
# sin contraseñas PPPoE, sin PSK de wireless, sin claves IPsec ni WireGuard y
# sin comunidades SNMP: el archivo existía, el panel lo daba por bueno y no
# servía para restaurar nada. Aquí se invierte el criterio: se pide
# `show-sensitive` salvo que se detecte positivamente una versión anterior a
# 6.43. Si no se puede leer la versión también se pide, porque un `/export`
# censurado es un daño silencioso y un argumento no soportado en un RouterOS
# antiquísimo es un fallo ruidoso, que se ve y se corrige.
#
# CAMBIO 2 — no reventar cuando no se puede leer la versión.
# El modelo original hacía `/([0-9])/.match(version_line)[0]` sobre un posible
# nil, y un router que no respondiera a `/system package update print` como se
# espera se quedaba sin ningún respaldo por un NoMethodError.
#
# Requisito en el router, sin el cual el CAMBIO 1 no sirve de nada: la cuenta de
# respaldo necesita la policy `sensitive` además de `ssh,read`.

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
    # CAMBIO 2: `.to_s` sobre el resultado del grep, que puede ser nil.
    version_line = cfg.each_line.grep(/installed-version:\s|current-version:\s/)[0].to_s
    @ros_version_full = version_line[/\d+(?:\.\d+)*/]
    comment version_line
  end

  cmd '/system history print without-paging' do |cfg|
    comment cfg
  end

  cmd :significant_changes do |cfg|
    cfg.gsub(/^(#\s+installed-version: [^\n]+\n).*?^(?=# software id)/m, '\1')
  end

  post do
    # CAMBIO 1: `show-sensitive` por defecto; `/export` a secas solo si se
    # detecta una versión anterior a 6.43, que es donde MikroTik invirtió el
    # comportamiento.
    run_cmd = if vars(:remove_secret)
                '/export hide-sensitive'
              elsif @ros_version_full &&
                    Gem::Version.new(@ros_version_full) < Gem::Version.new('6.43')
                '/export'
              else
                '/export show-sensitive'
              end
    logger.debug "Running #{run_cmd} for routeros version #{@ros_version_full.inspect}"
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
