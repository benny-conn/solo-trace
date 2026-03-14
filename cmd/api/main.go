package main

import (
	"log"

	"github.com/benny-conn/solo-grabber/internal/api"
	"github.com/benny-conn/solo-grabber/internal/store"
	"github.com/benny-conn/solo-grabber/internal/utils"
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

	addr := utils.ServerAddr()
	log.Printf("solo-grabber API listening on %s", addr)
	if err := router.Run(addr); err != nil {
		log.Fatalf("server error: %v", err)
	}
}
