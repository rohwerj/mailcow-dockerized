local rspamd_ip = require "rspamd_ip"
local rspamd_logger = require "rspamd_logger"
local fun = require 'fun'

rspamd_config:register_symbol{
    name = 'FWD_GETMAIL',
    type = 'prefilter',
    weight = 0.0,
    callback = function(task)

      -- Getmail runs on localhost
      local from_ip = task:get_from_ip()
      if not from_ip or not from_ip:is_local() then
        rspamd_logger.infox(task, 'Not a local IP so not checking')
        return false
      end

      local rcvd_hdrs = fun.filter(function(h)
        return not h['flags']['artificial']
      end, task:get_received_headers()):totable()

      -- Check if the last received header was added by Getmail
      local id = ".*( getmail6 ).*"
      if rcvd_hdrs[1] and string.match(rcvd_hdrs[1].raw, id) then
        -- Mark as forwarded
        rspamd_logger.infox(task, 'Found getmail6 in received header')
        task:insert_result('FORWARDED', 1.0)
      else
        rspamd_logger.infox(task, 'No getmail6 in received header')
        return false
      end

      local rcvd = rcvd_hdrs[2]
      if rcvd then
        rspamd_logger.infox(task, 'Using new received header %', rcvd)
	-- Set the previous hop's remote IP as from IP
        if rcvd.from_ip then
          local remote_rcvd_ip = rspamd_ip.from_string(rcvd.from_ip)

          if remote_rcvd_ip and remote_rcvd_ip:is_valid() and (not remote_rcvd_ip:is_local()) then
            task:set_from_ip(remote_rcvd_ip)
            task:disable_symbol('RCVD_NO_TLS_LAST')
          else
            rspamd_logger.errx(task, "invalid remote IP: %s", remote_rcvd_ip)
          end
        else
          rspamd_logger.errx(task, "no IP in header: %s", rcvd)
        end

        -- Set the previous hop's hostname as hostname and helo
        if rcvd.from_hostname then
          task:set_hostname(rcvd.from_hostname)
          task:set_helo(rcvd.from_hostname)
        else
          rspamd_logger.warnx(task, "no hostname in header: %s", rcvd)
        end
      else
        rspamd_logger.errx(task, "no previous received header")
      end

      return true
    end,
    priority = 10
}
