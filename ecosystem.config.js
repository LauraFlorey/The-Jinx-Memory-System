module.exports = {
  apps: [
    {
      name: "agent-discord",
      script: "python3",
      args: "discord-bot.py",
      cwd: __dirname,
      env_file: ".env",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_file: "logs/discord-bot-pm2.log",
      error_file: "logs/discord-bot-error.log",
    },
    {
      name: "agent-scheduler",
      script: "bash",
      args: "scheduler.sh cron-install",
      cwd: __dirname,
      env_file: ".env",
      autorestart: false,
    },
  ],
};
