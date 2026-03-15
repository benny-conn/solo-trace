package main

import (
	"log"

	"github.com/benny-conn/solo-trace/internal/api"
	sgcron "github.com/benny-conn/solo-trace/internal/cron"
	"github.com/benny-conn/solo-trace/internal/store"
	"github.com/benny-conn/solo-trace/internal/utils"
	"github.com/gin-gonic/gin"
	"github.com/spf13/viper"
)

func init() {
	utils.LoadConfig()
}

func main() {
	db, err := store.NewSQLiteStore(viper.GetString("DB_PATH"))
	if err != nil {
		log.Fatalf("failed to open database: %v", err)
	}

	handler := api.NewHandler(db)
	router := gin.Default()
	api.SetupRoutes(router, handler)

	if viper.GetBool("SMALLS_CRON_ENABLED") {
		c := sgcron.Start(db) //nolint:ineffassign
		defer c.Stop()
	}

	addr := utils.ServerAddr()
	log.Printf("solo-trace API listening on %s", addr)
	if err := router.Run(addr); err != nil {
		log.Fatalf("server error: %v", err)
	}
}
